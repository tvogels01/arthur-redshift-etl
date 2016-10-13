"""
Module for data warehouse configuration, initialization and user micro management.

initial_setup: Create groups, users, schemas in the data warehouse.

Assumes that the database has already been created in the data warehouse.
Drops PUBLIC schema. Requires entering the password for the ETL on the command
line or having a password in the .pgpass file.

If you need to re-run this (after adding available schemas in the
configuration), you should skip the user and group creation.


create_user: Create new user.  Optionally add a personal schema in the database.

The search path is set to the user's own schema and all the schemas
from the configuration in the order they are defined.  (Note that the user's
schema (as in "$user") comes first.)

Oddly enough, it is possible to skip the "create user" step but that comes in
handy when you want to update the user's search path.
"""

from contextlib import closing
import logging

from etl import join_with_quotes
import etl.commands
import etl.config
import etl.pg


def create_schemas(conn, schemas, owner=None):
    logger = logging.getLogger(__name__)
    logger.info("Dropping public schema")
    etl.pg.execute(conn, """DROP SCHEMA IF EXISTS PUBLIC CASCADE""")

    for schema in schemas:
        logger.info("Creating schema '%s', granting access to %s", schema.name, join_with_quotes(schema.groups))
        etl.pg.create_schema(conn, schema.name, owner)
        for owner_group in schema.owner_groups:
            etl.pg.grant_all_on_schema(conn, schema.name, owner_group)
        for reader_group in schema.reader_groups:
            etl.pg.grant_usage(conn, schema.name, reader_group)


def initial_setup(config, database_name, with_user_creation=False, dry_run=False):
    """
    Place named data warehouse database into initial state
        This destroys the contents of the targeted database.
        Optionally add with_users flag to create users and groups.
    """
    logger = logging.getLogger(__name__)
    if dry_run:
        logger.info("Dry run: skipping creation of required groups: %s", join_with_quotes(config.groups))
        logger.info("Dry run: skipping creation of required users: %s", join_with_quotes(config.users))
    else:
        with closing(etl.pg.connection(config.dsn_admin)) as conn:
            if with_user_creation:
                with conn:
                    logger.info("Creating required groups: %s", join_with_quotes(config.groups))
                    for group in config.groups:
                        etl.pg.create_group(conn, group)
                    for user in config.users:
                        logger.info("Creating user '%s' in group '%s' with empty search path", user.name, user.group)
                        etl.pg.create_user(conn, user.name, user.group)
                        etl.pg.alter_search_path(conn, user.name, ['public'])
    if not database_name:
        logger.info("No database specified to initialize")
        return
    if dry_run:
        logger.info("Dry run: Skipping drop & recreate of database '%s'", database_name)
    else:
        logger.info("Dropping and recreating database '%s'", database_name)
        autocommit_conn = etl.pg.connection(config.dsn_admin, autocommit=True)
        etl.pg.drop_and_create_database(autocommit_conn, database_name)
        logger.info("Dry run: skipping change of ownership over %s to ETL owner %s", database_name, config.owner)
        etl.pg.execute(autocommit_conn, """ALTER DATABASE "{}" OWNER TO "{}" """.format(database_name, config.owner))


def create_new_user(config, new_user, is_etl_user=False, add_user_schema=False, skip_user_creation=False):
    """
    Add new user to database within default user group and with new password.
    If so advised, creates a schema for the user (with the schema name the same as the name of the user).
    If so advised, adds the user to the ETL group, giving R/W access. Use wisely.

    This is safe to re-run as long as you skip creating users and groups the second time around.
    """
    logger = logging.getLogger(__name__)

    # Find user in the list of pre-defined users or create new user instance with default settings
    for user in config.users:
        if user.name == new_user:
            break
    else:
        user = etl.config.DataWarehouseUser({"name": new_user,
                                             "group": config.default_group,
                                             "schema": new_user})
    if user.name in ("default", config.owner):
        raise ValueError("Illegal user name '%s'" % user.name)

    with closing(etl.pg.connection(config.dsn_admin)) as conn:
        with conn:
            if not skip_user_creation:
                logger.info("Creating user '%s' in group '%s'", user.name, user.group)
                etl.pg.create_user(conn, user.name, user.group)
            if is_etl_user:
                logger.info("Adding user '%s' to ETL group '%s'", user.name, config.groups[0])
                etl.pg.alter_group_add_user(conn, config.groups[0], user.name)
            if add_user_schema:
                logger.info("Creating schema '%s' with owner '%s'", user.schema, user.name)
                etl.pg.create_schema(conn, user.schema, user.name)
                etl.pg.grant_all_on_schema(conn, user.schema, config.groups[0])
                etl.pg.grant_usage(conn, user.schema, user.group)
            # Non-system users have "their" schema in the search path, others get nothing (meaning just public).
            search_path = ["public"]
            if user.schema == user.name:
                search_path[:0] = ["'$user'"]  # needs to be quoted
            logger.info("Setting search path for user '%s' to: %s", user.name, ", ".join(search_path))
            etl.pg.alter_search_path(conn, user.name, search_path)


def ping(dsn):
    """
    Send a test query to the data warehouse
    """
    with closing(etl.pg.connection(dsn)) as conn:
        if etl.pg.ping(conn):
            print("{} is alive".format(etl.pg.dbname(conn)))
