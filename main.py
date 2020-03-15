from apscheduler.schedulers.blocking import BlockingScheduler as Scheduler
from apscheduler.executors.pool import ThreadPoolExecutor
import logging
import datetime

import tools as config
import dump

logger = logging.getLogger(__name__)

def next_month(current):
    if current[1] == 12:
        return (current[0] + 1, 1)
    return (current[0], current[1] + 1)

def all_months_since(start, end=None):
    if end is None:
        end = datetime.datetime.now().strftime("%Y-%m-%d")
    months = []
    month = start
    while True:
        date = "{:04d}-{:02d}-01".format(*month)
        if date > end:
            break
        months.append(date)
        month = next_month(month)
    return months

def check_all_month(params, bins):
    for i in range(1, len(bins)):
        left = bins[i-1]
        right = bins[i]
        p = config.default_search_params.copy()
        p.update(params)
        if left:
            p['scd'] = left
        if right:
            p['ecd'] = right
        dump.crawl_by_search(p, skip_exists=False)

bins_az = ["", "2017-09-01", ] + all_months_since((2017, 11)) + [""]
bins_fgo = ["", "2015-12-01", "2016-02-01", "2016-04-01"] + all_months_since((2016, 5)) + [""]
bins_all = ["", "2015-01-01", "2017-01-01", "2018-01-01", "2019-01-01", "2020-01-01", ""]

if __name__ == "__main__":
    scheduler = Scheduler(executers={"default": ThreadPoolExecutor(8)})

    scheduler.add_job(
        check_all_month, kwargs={'params': {'word': '10000users入り', 's_mode': 's_tag', 'mode': 'r18'}, 'bins': bins_all},
        trigger='cron', second='0', minute='0', hour='2', day_of_week='sun',
        id="check_all_month-All@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.crawl_by_search, kwargs={
            'search_params': {'word': '10000users入り', 's_mode': 's_tag', 'mode': 'r18'},
            'use_scd': 2,
        },
        trigger='cron', second='0', minute='0', hour='*',
        id="crawl_by_search-All@cron", misfire_grace_time=60*60
    )


    scheduler.add_job(
        check_all_month, kwargs={'params': {'word': 'アズールレーン'}, 'bins': bins_az},
        trigger='cron', second='0', minute='0', hour='2', day_of_week='sun',
        id="check_all_month-AZ@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.crawl_by_search, kwargs={
            'search_params': {'word': 'アズールレーン'},
            'use_scd': 2,
        },
        trigger='cron', second='0', minute='0', hour='*',
        id="crawl_by_search-AZ@cron", misfire_grace_time=60*60
    )

    scheduler.add_job(
        check_all_month, kwargs={'params': {'word': 'Fate/GrandOrder'}, 'bins': bins_fgo},
        trigger='cron', second='0', minute='0', hour='2', day_of_week='sun',
        id="check_all_month-FGO@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.crawl_by_search, kwargs={
            'search_params': {'word': 'Fate/GrandOrder'},
            'use_scd': 2,
        },
        trigger='cron', second='0', minute='0', hour='*',
        id="crawl_by_search-FGO@cron", misfire_grace_time=60*60
    )

    scheduler.add_job(
        dump.crawl_detail,
        trigger='cron', second='0', minute='*/5', hour='*',
        id="crawl_detail@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.crawl_anime_info,
        trigger='cron', second='0', minute='10', hour='*',
        id="crawl_anime_info@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.crawl_illust_file,
        trigger='cron', second='50', minute='*/10', hour='*',
        id="crawl_illust_file@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.crawl_anime_file,
        trigger='cron', second='0', minute='*/20', hour='*',
        id="crawl_anime_file@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.reset_statistics,
        trigger='cron', second='0', minute='0', hour='0',
        id="reset_statistics@cron", misfire_grace_time=60*60
    )
    scheduler.add_job(
        dump.show_statistics,
        trigger='cron', second='30', minute='*/5', hour='*',
        id="show_statistics@cron", misfire_grace_time=60*60
    )

    scheduler.start()
