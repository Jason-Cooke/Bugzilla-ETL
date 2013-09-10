################################################################################
## This Source Code Form is subject to the terms of the Mozilla Public
## License, v. 2.0. If a copy of the MPL was not distributed with this file,
## You can obtain one at http://mozilla.org/MPL/2.0/.
################################################################################
## Author: Kyle Lahnakoski (kyle@lahnakoski.com)
################################################################################

import json
from datetime import datetime

from bzETL.bz_etl import etl
from bzETL.extract_bugzilla import get_bugs_table_columns
from bzETL.util.cnv import CNV
from bzETL.util.db import DB, SQL
from bzETL.util.logs import Log
from bzETL.util.elasticsearch import ElasticSearch
from bzETL.util.files import File
from bzETL.util.query import Q
from bzETL.util.randoms import Random
from bzETL.util.startup import startup
from bzETL.util.strings import json_scrub
from bzETL.util.struct import Struct
from bzETL.util.timer import Timer

from util import compare_es
from util.compare_es import get_all_bug_versions
from util.fake_es import Fake_ES

def main(settings):

    #MAKE HANDLES TO CONTAINERS
    with DB(settings.bugzilla) as db:
        #REAL ES
#        if settings.candidate.alias is None:
#            settings.candidate.alias=settings.candidate.index
#            settings.candidate.index=settings.candidate.alias+CNV.datetime2string(datetime.utcnow(), "%Y%m%d_%H%M%S")
#        candidate=ElasticSearch.create_index(settings.candidate, File(settings.candidate.schema_file).read())
        candidate=Fake_ES(settings.fake_es)

        reference=ElasticSearch(settings.reference)

        #SETUP RUN PARAMETERS
        param=Struct()
        param.BUGS_TABLE_COLUMNS=get_bugs_table_columns(db, settings.bugzilla.schema)
        param.BUGS_TABLE_COLUMNS_SQL=SQL(",\n".join(["`"+c.column_name+"`" for c in param.BUGS_TABLE_COLUMNS]))
        param.BUGS_TABLE_COLUMNS=Q.select(param.BUGS_TABLE_COLUMNS, "column_name")
        param.END_TIME=CNV.datetime2milli(datetime.utcnow())
        param.START_TIME=0
        param.alias_file=settings.param.alias_file
        param.BUG_IDS_PARTITION=SQL("bug_id in {{bugs}}", {"bugs":db.quote_value(settings.param.bugs)})

        etl(db, candidate, param)

        #COMPARE ALL BUGS
        problems=compare_both(candidate, reference, settings, settings.param.bugs)
        if problems:
            Log.error("DIFFERENCES FOUND")

            

def random_sample_of_bugs(settings):
    """
    I USE THIS TO FIND BUGS THAT CAUSE MY CODE PROBLEMS.  OF COURSE, IT ONLY WORKS
    WHEN I HAVE A REFERENCE TO COMPARE TO
    """


    NUM_TO_TEST=100
    MAX_BUG_ID=900000


    with DB(settings.bugzilla) as db:
        candidate=Fake_ES(settings.fake_es)
        reference=ElasticSearch(settings.reference)

        #GO FASTER BY STORING LOCAL FILE
        local_cache=File(settings.param.temp_dir+"/private_bugs.json")
        if local_cache.exists:
            private_bugs=set(CNV.JSON2object(local_cache.read()))
        else:
            with Timer("get private bugs"):
                private_bugs= compare_es.get_private_bugs(reference)
                local_cache.write(CNV.object2JSON(private_bugs))

        while True:
            some_bugs=[b for b in [Random.int(MAX_BUG_ID) for i in range(NUM_TO_TEST)] if b not in private_bugs]

            #SETUP RUN PARAMETERS
            param=Struct()
            param.BUGS_TABLE_COLUMNS=get_bugs_table_columns(db, settings.bugzilla.schema)
            param.BUGS_TABLE_COLUMNS_SQL=SQL(",\n".join(["`"+c.column_name+"`" for c in param.BUGS_TABLE_COLUMNS]))
            param.BUGS_TABLE_COLUMNS=Q.select(param.BUGS_TABLE_COLUMNS, "column_name")
            param.END_TIME=CNV.datetime2milli(datetime.utcnow())
            param.START_TIME=0
            param.alias_file=settings.param.alias_file
            param.BUG_IDS_PARTITION=SQL("bug_id in {{bugs}}", {"bugs":db.quote_value(some_bugs)})

            try:
                etl(db, candidate, param)

                #COMPARE ALL BUGS
                found_errors=compare_both(candidate, reference, settings, some_bugs)
                if found_errors:
                    Log.note("Errors found")
                    break
                else:
                    pass
            except Exception, e:
                Log.warning("Total failure during compare of bugs {{bugs}}", {"bugs":some_bugs}, e)


#COMPARE ALL BUGS
def compare_both(candidate, reference, settings, some_bugs):
    File(settings.param.errors).delete()

    found_errors=False
    for bug_id in some_bugs:
        try:
            versions = Q.sort(
                get_all_bug_versions(candidate, bug_id, datetime.utcnow()),
                "modified_ts")
            # WE CAN NOT EXPECT candidate TO BE UP TO DATE BECAUSE IT IS USING AN OLD IMAGE
            if len(versions)==0:
                max_time = CNV.milli2datetime(settings.bugzilla.expires_on)
            else:
                max_time = CNV.milli2datetime(versions[-1].modified_ts)

            ref_versions = \
                Q.sort(
                    map(
                        lambda x: compare_es.old2new(x, settings.bugzilla.expires_on),
                        get_all_bug_versions(reference, bug_id, max_time)
                    ),
                    "modified_ts"
                )

            can = json.dumps(json_scrub(versions), indent=4, sort_keys=True, separators=(',', ': '))
            ref = json.dumps(json_scrub(ref_versions), indent=4, sort_keys=True, separators=(',', ': '))
            if can != ref:
                found_errors=True
                File(settings.param.errors + "/try/" + unicode(bug_id) + ".txt").write(can)
                File(settings.param.errors + "/exp/" + unicode(bug_id) + ".txt").write(ref)
        except Exception, e:
            found_errors=True
            Log.warning("Problem ETL'ing bug {{bug_id}}", {"bug_id":bug_id})

    return found_errors



def test_etl():
    try:
        settings=startup.read_settings()
        Log.start(settings.debug)
#        random_sample_of_bugs(settings)
        main(settings)
    finally:
        Log.stop()

test_etl()

