import logging
import os.path
import shlex
import subprocess
from contextlib import closing
from tempfile import NamedTemporaryFile
from typing import Dict, List, Union

import etl.config
import etl.monitor
import etl.pg
import etl.s3
from etl.config.dw import DataWarehouseSchema
from etl.errors import SqoopExecutionError
from etl.extract.extractor import Extractor
from etl.extract.partition import DefaultPartitioningStrategy
from etl.names import TableName
from etl.relation import RelationDescription


class SqoopExtractor(Extractor):

    """
    This extractor takes care of all the idiosyncrasies of extracting data from
    upstream sources using Sqoop, http://sqoop.apache.org/
    """

    def __init__(self, schemas: Dict[str, DataWarehouseSchema], relations: List[RelationDescription],
                 keep_going: bool, max_partitions: int, dry_run: bool) -> None:
        super().__init__("sqoop", schemas, relations, keep_going, needs_to_wait=True, dry_run=dry_run)
        self.logger = logging.getLogger(__name__)
        self.max_partitions = max_partitions
        self.sqoop_executable = "sqoop"

        # During Sqoop extraction we write out files to a temp location
        self._sqoop_options_dir = etl.config.etl_tmp_dir("sqoop")
        if not os.path.isdir(self._sqoop_options_dir) and not self.dry_run:
            self.logger.info("Creating directory '%s' (with mode 750)", self._sqoop_options_dir)
            os.makedirs(self._sqoop_options_dir, mode=0o750, exist_ok=True)

    def extract_table(self, source: DataWarehouseSchema, relation: RelationDescription) -> None:
        """
        Run Sqoop for one table; creates the sub-process and all the pretty args for Sqoop.
        """
        args = self.build_sqoop_options(source.dsn, relation)
        self.logger.debug("Sqoop options are:\n%s", " ".join(args))
        options_file = self.write_options_file(args)

        self._delete_directory_before_write(relation)
        self.run_sqoop(options_file)
        prefix = os.path.join(relation.prefix, relation.csv_path_name)
        self.write_manifest_file(relation, relation.bucket_name, prefix)

    def write_password_file(self, password: str) -> str:
        """
        Write password to a (temporary) file, return name of file created.
        """
        if self.dry_run:
            self.logger.info("Dry-run: Skipping writing of password file")
            password_file_path = "/tmp/never_used"
        else:
            with NamedTemporaryFile('w', dir=self._sqoop_options_dir, prefix="pw_", delete=False) as fp:
                fp.write(password)  # type: ignore
                fp.close()
            password_file_path = fp.name
            self.logger.info("Wrote password to '%s'", password_file_path)
        return password_file_path

    def write_connection_params(self) -> str:
        """
        Write a (temporary) file for connection parameters, return name of file created.
        """
        if self.dry_run:
            self.logger.info("Dry-run: Skipping writing of connection params file")
            params_file_path = "/tmp/never_used"
        else:
            with NamedTemporaryFile('w', dir=self._sqoop_options_dir, prefix="cp_", delete=False) as fp:
                fp.write("ssl = true\n")  # type: ignore
                fp.write("sslfactory = org.postgresql.ssl.NonValidatingFactory\n")  # type: ignore
                fp.close()
            params_file_path = fp.name
            self.logger.info("Wrote connection params to '%s'", params_file_path)
        return params_file_path

    def build_sqoop_options(self, source_dsn: Dict[str, str], relation: RelationDescription) -> List[str]:
        """
        Create set of Sqoop options.

        Starts with the command (import), then continues with generic options,
        tool specific options, and child-process options.
        """
        jdbc_url, dsn_properties = etl.pg.extract_dsn(source_dsn)

        password_file_path = self.write_password_file(dsn_properties["password"])
        params_file_path = self.write_connection_params()

        source_table_name = relation.source_table_name
        columns = relation.get_columns_with_casts()
        select_statement = """SELECT {} FROM {} WHERE $CONDITIONS""".format(", ".join(columns), source_table_name)

        # Only the paranoid survive ... quote arguments of options, except for --select
        def q(s):
            # E731 do not assign a lambda expression, use a def -- whatever happened to Python?
            return '"{}"'.format(s)

        args = ["import",
                "--connect", q(jdbc_url),
                "--driver", q("org.postgresql.Driver"),
                "--connection-param-file", q(params_file_path),
                "--username", q(dsn_properties["user"]),
                "--password-file", '"file://{}"'.format(password_file_path),
                "--verbose",
                "--fields-terminated-by", q(","),
                "--lines-terminated-by", r"'\n'",
                "--enclosed-by", "'\"'",
                "--escaped-by", r"'\\'",
                "--null-string", r"'\\N'",
                "--null-non-string", r"'\\N'",
                # NOTE Does not work with s3n:  "--delete-target-dir",
                "--target-dir", '"s3n://{}/{}/{}"'.format(relation.bucket_name,
                                                          relation.prefix,
                                                          relation.csv_path_name),
                # NOTE Quoting the select statement (e.g. with shlex.quote) breaks the select in an unSQLy way.
                "--query", select_statement,
                # NOTE Embedded newlines are not escaped so we need to remove them.  WAT?
                "--hive-drop-import-delims",
                "--compress"]  # The default compression codec is gzip.

        args.extend(self.build_sqoop_partition_options(source_dsn, relation))

        return args

    def build_sqoop_partition_options(self, source_dsn: Dict[str, str], relation: RelationDescription) -> List[str]:
        """
        Build the partitioning-related arguments for Sqoop.
        """
        partition_key = relation.find_partition_key()
        if partition_key:
            quoted_key_arg = '"{}"'.format(partition_key)
            num_mappers = self.determine_partitioning(source_dsn, relation.source_table_name)
            return ["--split-by", quoted_key_arg, "--num-mappers", str(num_mappers)]
        else:
            # TODO use "--autoreset-to-one-mapper" ?
            return ["--num-mappers", "1"]

    def determine_partitioning(self, source_dsn: Dict[str, str], source_table_name: TableName) -> int:
        with closing(etl.pg.connection(source_dsn, readonly=True)) as conn:
            table_size = etl.pg.fetch_table_size(conn, source_table_name.identifier)
            num_partitions = DefaultPartitioningStrategy(table_size, self.max_partitions).calculate()

        return num_partitions

    def write_options_file(self, args: List[str]) -> str:
        """
        Write options to a (temporary) file, return name of file created.
        """
        if self.dry_run:
            self.logger.info("Dry-run: Skipping creation of Sqoop options file")
            options_file_path = "/tmp/never_used"
        else:
            with NamedTemporaryFile('w', dir=self._sqoop_options_dir, prefix="so_", delete=False) as fp:
                fp.write('\n'.join(args))  # type: ignore
                fp.write('\n')  # type: ignore
                fp.close()
            options_file_path = fp.name
            self.logger.info("Wrote Sqoop options to '%s'", options_file_path)
        return options_file_path

    def _delete_directory_before_write(self, relation: RelationDescription) -> None:
        """
        Need to first delete data directory since Sqoop won't overwrite (and can't delete).
        """
        csv_prefix = os.path.join(relation.prefix, relation.csv_path_name)
        deletable = sorted(etl.s3.list_objects_for_prefix(relation.bucket_name, csv_prefix))
        if deletable:
            if self.dry_run:
                self.logger.info("Dry-run: Skipping deletion of existing CSV files in 's3://%s/%s'",
                                 relation.bucket_name, csv_prefix)
            else:
                etl.s3.delete_objects(relation.bucket_name, deletable)

    def run_sqoop(self, options_file_path: str):
        """
        Run Sqoop in a sub-process with the help of the given options file.
        """
        args = [self.sqoop_executable, "--options-file", options_file_path]
        cmdline = " ".join(map(shlex.quote, args))
        if self.dry_run:
            self.logger.info("Dry-run: Skipping Sqoop run '%s'", cmdline)
        else:
            self.logger.debug("Starting: %s", cmdline)
            sqoop = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     universal_newlines=True)
            self.logger.debug("Sqoop is running with pid %d", sqoop.pid)
            out, err = sqoop.communicate()
            nice_out, nice_err = ('\n' + str(out)).rstrip(), ('\n' + str(err)).rstrip()  # using str() for type check
            self.logger.debug("Sqoop finished with return code %d", sqoop.returncode)
            self.logger.debug("Sqoop stdout:%s", nice_out)
            self.logger.debug("Sqoop stderr:%s", nice_err)
            if sqoop.returncode != 0:
                raise SqoopExecutionError("Sqoop failed with return code %s" % sqoop.returncode)


class FakeSqoopExtractor(SqoopExtractor):
    """
    This extractor runs '/usr/bin/false' which means that extraction fails ... used for testing outside EMR.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sqoop_executable = "/usr/bin/false"
