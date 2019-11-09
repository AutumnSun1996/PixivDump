from apscheduler.schedulers.blocking import BlockingScheduler as Scheduler
from apscheduler.executors.pool import ThreadPoolExecutor
import logging

import config
import dump

logger = logging.getLogger(__name__)


def update_params_by_height(cond, lvls):
    lvls = lvls.copy()
    if lvls[0] is not None:
        lvls.insert(0, None)
    if lvls[-1] is not None:
        lvls.append(None)
    
    for i in range(len(lvls)-1):
        params = cond.copy()
        left = lvls[i]
        right = lvls[i+1]
        if left:
            params['hlt'] = left
        if right:
            params['hgt'] = right
        yield params


scheduler = Scheduler(executers={"default": ThreadPoolExecutor(8)})

for params in update_params_by_height(
    {'s_mode': 's_tag_full', 'word': 'アズールレーン', 'order': 'date_d'},
    [1200,2000],
):
    scheduler.add_job(
        dump.crawl_by_search, kwargs={'params': params},
        trigger='cron', second='0', minute='0', hour='*',
        id="crawl_by_search-"+str(params), misfire_grace_time=60*60
    )

for params in update_params_by_height(
    {'s_mode': 's_tag_full', 'word': 'Fate/GrandOrder', 'order': 'date_d'},
    [700,800,900,1000,1050,1100,1200,1300,1500,1700,2000,2500,3500],
):
    scheduler.add_job(
        dump.crawl_by_search, kwargs={'params': params},
        trigger='cron', second='0', minute='0', hour='*',
        id="crawl_by_search-"+str(params), misfire_grace_time=60*60
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
