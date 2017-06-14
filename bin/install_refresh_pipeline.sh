#!/usr/bin/env bash

if [[ $# -lt 5 || "$1" = "-h" ]]; then
    echo "Usage: `basename $0` <bucket_name> <environment> <startdatetime> <occurrences> <source table selection> [<source table selection> ...]"
    echo "      Start time should take the ISO8601 format like: `date -u +"%Y-%m-%dT%H:%M:%S"`"
    echo "      Specify source tables using space-delimited arthur pattern globs."
    exit 0
fi

set -e -u

function join_by { local IFS="$1"; shift; echo "$*"; }

PROJ_BUCKET="$1"
PROJ_ENVIRONMENT="$2"

START_DATE_TIME="$3"
OCCURRENCES="$4"

BINDIR=`dirname $0`
TOPDIR=`\cd $BINDIR/.. && \pwd`
CONFIG_SOURCE="$TOPDIR/aws_config"

if [[ ! -d "$CONFIG_SOURCE" ]]; then
    echo "Cannot find configuration files (aws_config)"
    exit 1
else
    echo "Using local configuration files in $CONFIG_SOURCE"
fi

shift 4
SELECTION="$@"
C_S_SELECTION="$(join_by ',' $SELECTION)"

# Verify that this bucket/environment pair is set up on s3
BOOTSTRAP="s3://$PROJ_BUCKET/$PROJ_ENVIRONMENT/current/bin/bootstrap.sh"
if ! aws s3 ls "$BOOTSTRAP" > /dev/null; then
    echo "Check whether the bucket \"$PROJ_BUCKET\" and folder \"$PROJ_ENVIRONMENT\" exist!"
    exit 1
fi

set -x

if [[ "$PROJ_ENVIRONMENT" =~ "production" ]]; then
    PIPELINE_TAGS="key=DataWarehouseEnvironment,value=Production"
else
    PIPELINE_TAGS="key=DataWarehouseEnvironment,value=Development"
fi
PIPELINE_NAME="ETL Refresh Pipeline ($PROJ_ENVIRONMENT @ $START_DATE_TIME, N=$OCCURRENCES)"

PIPELINE_ID_FILE="/tmp/pipeline_id_${USER}_$$.json"

aws datapipeline create-pipeline \
    --unique-id refresh-etl-pipeline \
    --name "$PIPELINE_NAME" \
    --tags "$PIPELINE_TAGS" \
    | tee "$PIPELINE_ID_FILE"

PIPELINE_ID=`jq --raw-output < "$PIPELINE_ID_FILE" '.pipelineId'`

if [[ -z "$PIPELINE_ID" ]]; then
    set +x
    echo "Failed to find pipeline id in output -- pipeline probably wasn't created. Check your VPN etc."
    exit 1
fi

aws datapipeline put-pipeline-definition \
    --pipeline-definition file://${CONFIG_SOURCE}/refresh_pipeline.json \
    --parameter-values \
        myS3Bucket="$PROJ_BUCKET" \
        myEtlEnvironment="$PROJ_ENVIRONMENT" \
        myStartDateTime="$START_DATE_TIME" \
        myOccurrences="$OCCURRENCES" \
        mySelection="$SELECTION" \
        myCommaSeparatedSelection="$C_S_SELECTION" \
    --pipeline-id "$PIPELINE_ID"

aws datapipeline activate-pipeline --pipeline-id "$PIPELINE_ID"