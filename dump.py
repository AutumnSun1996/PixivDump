import re
import json
import math
import json
import datetime

import pymongo
import gridfs
import pyquery
from requests_futures.sessions import FuturesSession
import logging

import config
logger = logging.getLogger(__name__)

db = pymongo.MongoClient(**config.mongo_kwargs)[config.mongo_db_name]
fs = gridfs.GridFS(db)

session = FuturesSession(adapter_kwargs={'max_retries': 3})

task_statistics = {'total': 0, 'done': 0, 'success': 0}

def extra_args(func, *extra_args, **extra_kwargs):
    """为待调用的函数添加额外参数"""
    def new_func(*args, **kwargs):
        args = extra_args + args
        extra_kwargs.update(kwargs)
        return func(*args, **extra_kwargs)
    return new_func

def update_result(r, target, *args, **kwargs):
    target['done'] += 1
    if r.handler_error is None:
        target['success'] += 1

def send_request(method, url, **kwargs):
    target = task_statistics # make a ref of task_statistics, in case it reset between the exec of following two lines
    target['total'] += 1
    hook_func = extra_args(update_result, target=target)
    # update hooks, set 'update_result' as the last hook
    if 'hooks' not in kwargs:
        kwargs['hooks'] = {}
    if 'response' not in kwargs['hooks']:
        kwargs['hooks']['response'] = [hook_func]
    elif callable(kwargs['hooks']['response']):
        kwargs['hooks']['response'] = [kwargs['hooks']['response'], hook_func]
    elif isinstance(kwargs['hooks']['response'], (list, tuple)):
        hooks = []
        hooks.extend(kwargs['hooks']['response'])
        hooks.append(hook_func)
        kwargs['hooks']['response'] = hooks
    return session.request(method, url, **kwargs)

def get_statistics():
    info = task_statistics.copy()
    info['failed'] = info['done'] - info['success']
    info['running'] = info['total'] - info['done']
    return info

def show_statistics():
    logger.warning('current statistics: %s', get_statistics())

def reset_statistics():
    global task_statistics
    logger.warning('last statistics: %s', get_statistics())
    task_statistics = {'total': 0, 'done': 0, 'success': 0}

def update_search(response, *args, **kwargs):
    """处理搜索结果"""
    try:
        pq = pyquery.PyQuery(response.text)
        items = json.loads(pq("#js-mount-point-search-result-list").attr("data-items"))
        for origin_item in items:
            if 'illustId' not in origin_item:
                continue
            try:
                item = {}
                for key in config.key_names['search']:
                    item[key] = origin_item[key]
                item['updateTime'] = datetime.datetime.now()
                db.illust.insert_one(item)
            except pymongo.errors.DuplicateKeyError:
                item.pop("_id")
                pid = item.pop('illustId')
                db.illust.update_one({'illustId': pid}, {'$set': item})
        response.handler_error = None
    except Exception as e:
        response.handler_error = e
        logger.exception("update_search failed for %s: %s", response.url, e)

def try_update_illust(illust, update_time):
    try:
        item = {}
        for key in config.key_names['search']:
            if key in illust:
                item[key] = illust[key]
        item['updateTime'] = update_time
        db.illust.update_one({'illustId': illust['illustId']}, {'$set': item}, upsert=True)
    except Exception as e:
        logger.exception("try_update_illust failed for %s: %s", illust, e)

def update_detail(response, pid, *args, **kwargs):
    """处理详情信息"""
    try:
        now = datetime.datetime.now()
        item = {'detail': {}, 'updateTime': now}
        history = {'updateTime': now}
        info = response.json()
        if info['error']:
            if not info['body']:
                info.pop('body')
            item['detail'] = info
        else:
            origin_item = info['body']
            for key in config.key_names['detail']:
                item['detail'][key] = origin_item[key]
            for key in config.key_names['detail2search']:
                item[key] = origin_item[key]
            for key in config.key_names['detail2history']:
                history[key] = origin_item[key]
            # 更新 tags
            item['tags'] = [t['tag'] for t in origin_item['tags']['tags']]
            url = origin_item['urls']['original']
            idx = url.rfind('.')
            item['imageUrlFormat'] = url[:idx-1] + "{pageIndex}" + url[idx:]

            # 尝试根据 userIllusts 更新其他插画的信息
            for other in origin_item.get('userIllusts', {}).values():
                if other is None or pid != other['illustId']:
                    continue
                try_update_illust(other, item['updateTime'])

        db.illust.update_one({'illustId': pid}, {'$set': item}, upsert=True)
        db.illustHistory.insert_one(history)
        response.handler_error = None
    except Exception as e:
        response.handler_error = e
        logger.exception("update_detail failed for %s(%s): %s", pid, response.url, e)

def update_ugoira_meta(response, pid, *args, **kwargs):
    """处理动画信息"""
    try:
        logger.debug('update_ugoira_meta for %s start', pid)
        item = {'updateTime': datetime.datetime.now()}
        info = response.json()
        if info['error']:
            if not info['body']:
                info.pop('body')
            item['frameInfo'] = info
        else:
            item['frameInfo'] = info['body']
        db.illust.update_one({'illustId': pid}, {'$set': item})
        response.handler_error = None
        logger.debug('update_ugoira_meta for %s succeed', pid)
    except Exception as e:
        response.handler_error = e
        logger.exception("update_ugoira_meta failed for %s(%s): %s", pid, response.url, e)

def save_illust(response, pid, index, **kwargs):
    """保存文件"""
    try:
        logger.debug('save_illust for %s start', pid)
        info = {'illustId': pid, 'pageIndex': index, 'fileType': 'illust'}
        url = response.url
        
        if fs.exists(filename=url):
            file_id = fs.get_last_version(filename=url)._id
            logger.warning('ignore existing file: %s(%s)', pid, url)
        else:
            file_id = fs.put(filename=url, data=response.content, metadata=info)

        info = {'filename': url, 'pageIndex': index, 'fileId': file_id}
        res = db.illust.update_one(
            {'illustId': pid, 'files': {'$not': {'$elemMatch': {'pageIndex': index}}}}, 
            {'$push': {
                'files': {
                    '$each': [info],
                    '$sort': {'pageIndex': 1}
                }
            }, '$inc': {'fileCount': 1}})
        if not res.raw_result['n']:
            logger.warning('save_illust for %s(%s) update result: %s', pid, url, res.raw_result)

        logger.debug('save_illust for %s succeed', pid)
        response.handler_error = None
    except Exception as e:
        response.handler_error = e
        logger.exception("save_illust failed for %s(%s): %s", pid, response.url, e)

def save_file(response, pid, **kwargs):
    """保存文件"""
    try:
        logger.debug('save_file for %s start', pid)
        info = {'illustId': pid, 'fileType': 'frames'}
        url = response.url
        if fs.exists(filename=url):
            file_id = fs.get_last_version(filename=url)._id
            logger.warning('ignore existing file: %s(%s)', pid, url)
        else:
            file_id = fs.put(filename=url, data=response.content, metadata=info)

        info = {'filename': url, 'fileId': file_id}
        res = db.illust.update_one(
            {'illustId': pid}, 
            {'$set': {
                'frameInfo.file': info
            }})
        response.handler_error = None
        logger.debug('save_file for %s succeed', pid)
    except Exception as e:
        response.handler_error = e
        logger.exception("save_file failed for %s(%s): %s", pid, response.url, e)

def crawl_by_search(params, skip_exists=True, page_limit=100):
    # 第一页
    logger.debug("start crawl_by_search of fisrt page for %s", params)
    future = send_request(
        'GET', "https://210.140.131.182:443/search.php",
        params=params,
        timeout=config.TIMEOUT,
        verify=False, 
        headers=config.HEADERS['search'],
        hooks={
            'response': update_search,
        },
    )
    res = future.result()
    pq = pyquery.PyQuery(res.text)
    total = pq('.count-badge').text()
    n_pics = int(total[:-1])
    n_pages = math.ceil(n_pics / 40)
    logger.info("found %s pictures, in %s pages", n_pics, n_pages)
    
    compare_cond = [{'detail.error': {'$exists': 0}}]
    if params.get('s_mode') is None:
        compare_cond += [{'tags': {'$regex': params['word']}}]
    elif params['s_mode'] == 's_tag_full':
        compare_cond += [{'tags': {'$eq': params['word']}}]
    elif params['s_mode'] == 's_tc':
        compare_cond += [{'content': {'$regex': params['word']}}]
    else:
        logger.error("invalid s_mode: %s", params['s_mode'])
    
    if 'type' in params:
        local_key = {'illust': 0, 'manga': 1, 'ugoira': 2}.get(params['type'])
        compare_cond += [{'illustType': {'$eq': local_key}}]

    if 'mode' in params:
        if params['mode'].lower() == 'r18':
            compare_cond += [{'tags': {'$eq': 'R-18'}}]
        else:
            compare_cond += [{'tags': {'$ne': 'R-18'}}]
    
    if 'wlt' in params:
        compare_cond += [{'width': {'$gte': params['wlt']}}]
    if 'wgt' in params:
        compare_cond += [{'width': {'$lte': params['wgt']}}]
    if 'hlt' in params:
        compare_cond += [{'width': {'$gte': params['hlt']}}]
    if 'hgt' in params:
        compare_cond += [{'width': {'$lte': params['hgt']}}]
    
    local_count = db.illust.count_documents({'$and': compare_cond})
    logger.info("found %s pictures in database for cond %s", local_count, compare_cond)

    if skip_exists:
        n_pics_more = n_pics - local_count
    else:
        n_pics_more = n_pics

    n_pages_more = min(math.ceil(n_pics_more / 40), page_limit, 1000) - 1
    if n_pages_more < 0:
        n_pages_more = 0
    n_pics_more = min(n_pics_more, n_pages_more * 40)
    logger.info("will crawl %s pages for %s more pictures", n_pages_more, n_pics_more)

    # 其余页
    for i in range(2, n_pages_more + 2):
        p = params.copy()
        p['p'] = i
        r = send_request(
            'GET', "https://210.140.131.182:443/search.php", 
            params=p,
            timeout=config.TIMEOUT,
            verify=False, 
            headers=config.HEADERS['search'],
            hooks={
                'response': update_search,
            }
        )
        logger.debug("start crawl_by_search for %s", p)

def crawl_detail_by_id(illust_id):
    header = config.HEADERS['detail'].copy()
    header['Referer'] = 'https://www.pixiv.net/artworks/'+illust_id
    r = send_request(
        'GET', 'https://210.140.131.182:443/ajax/illust/'+illust_id,
        timeout=config.TIMEOUT,
        verify=False,
        headers=header,
        hooks={
            'response': extra_args(update_detail, pid=illust_id),
        }
    )
    logger.debug("start crawl_detail for %s", illust_id)

def crawl_detail(limit=1000):
    cond = {'detail': {'$exists': 0}}
    n = db.illust.count_documents(cond)
    logger.info("crawl_detail of %s in %s illusts for condition: %s", limit, n, cond)

    for p in db.illust.find(
        cond, {'_id': 0, 'illustId': 1, 'bookmarkCount': 1}
        ).sort('bookmarkCount', pymongo.DESCENDING).limit(limit):
        crawl_detail_by_id(p['illustId'])
    if n >= limit:
        return
    limit -= n
    now = datetime.datetime.now()
    today = datetime.datetime(now.year, now.month, now.day)
    cond = {'updateTime': {'$lt': today}, 'detail.error': {'$ne': True}}
    n = db.illust.count_documents(cond)
    logger.info("crawl_detail of %s in %s illusts for condition: %s", limit, n, cond)
    for p in db.illust.find(
        cond, {'_id': 0, 'illustId': 1}
        ).sort('updateTime', pymongo.ASCENDING).limit(limit):
        crawl_detail_by_id(p['illustId'])

def crawl_anime_info():
    # 将动画信息更新到frameInfo
    cond = {'frameInfo': {'$exists': 0}, 'illustType': 2}
    n = db.illust.count_documents(cond)
    logger.info("crawl_anime_info of %s in %s illusts for condition: %s", n, n, cond)

    for p in db.illust.find(
        cond, {'_id': 0, 'illustId': 1}
        ).sort('bookmarkCount', pymongo.DESCENDING):
        pid = p['illustId']
        header = config.HEADERS['detail'].copy()
        header['Referer'] = 'https://www.pixiv.net/artworks/{}'.format(pid)
        r = send_request(
            'GET', 'https://210.140.131.182:443/ajax/illust/{}/ugoira_meta'.format(pid),
            timeout=config.TIMEOUT,
            verify=False,
            headers=header,
            hooks={
                'response': extra_args(update_ugoira_meta, pid=pid),
            }
        )
        logger.debug("start crawl_anime_info for %s", pid)

def download_illust(p):
    for i in range(p['pageCount']):
        url = p['imageUrlFormat'].format(pageIndex=i)
        if fs.exists(filename=url):
            continue
        r = send_request(
            'GET', url.replace('i.pximg.net', '210.140.92.140:443'),
            timeout=config.TIMEOUT,
            verify=False,
            headers={
                'Sec-Fetch-Mode': 'no-cors',
                'Host': 'i.pximg.net',
                'Referer': 'https://www.pixiv.net/artworks/' + p['illustId'],
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90'
            },
            hooks={
                'response': extra_args(save_illust, pid=p['illustId'], index=i),
            }
        )
        logger.debug("start download_illust at %s", url)

def download_ugoira(p):
    url = p['frameInfo']['originalSrc']
    if fs.exists(filename=url):
        return
    r = send_request(
        'GET', url.replace('i.pximg.net', '210.140.92.140:443'),
        timeout=config.TIMEOUT,
        verify=False,
        headers={
            'Sec-Fetch-Mode': 'no-cors',
            'Host': 'i.pximg.net',
            'Referer': 'https://www.pixiv.net/artworks/' + p['illustId'],
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90'
        },
        hooks={
            'response': extra_args(save_file, pid=p['illustId']),
        }
    )
    logger.debug("start download_ugoira at %s", url)

def crawl_illust_file(limit=100):
    # 下载图片
    n = next(db.illust.aggregate([
        {"$match":{"bookmarkCount": {'$gte': config.illust_min_bookmarks}}},
        {"$addFields": {
            "countMatch": {"$eq":["$pageCount","$fileCount"]}
        }},
        {"$match":{"countMatch": False}},
        {'$count': 'n'}
    ]))['n']
    logger.info("crawl_illust_file of %s in %s illusts", limit, n)
    for p in db.illust.aggregate([
        {"$match":{"bookmarkCount": {'$gte': config.illust_min_bookmarks}}},
        {"$addFields": {
            "countMatch": {"$eq":["$pageCount","$fileCount"]}
        }},
        {"$match":{"countMatch": False}},
        {'$project': {'illustId': 1, 'illustTitle': 1, 'imageUrlFormat': 1, 'pageCount': 1, 'bookmarkCount': 1}},
        {'$sort': {'bookmarkCount': -1}}, # 按收藏数降序排列
        {'$limit': limit} # 取前limit张图片
    ]):
        download_illust(p)
        logger.debug("start crawl_illust for %s", p['illustId'])

def crawl_anime_file(limit=100):
    cond = {
        'bookmarkCount': {'$gte': config.anime_min_bookmarks}, 
        'frameInfo.originalSrc': {'$exists': 1}, 
        'frameInfo.file': {'$exists': 0}
    }
    n = db.illust.count_documents(cond)
    logger.info("crawl_anime_file of %s in %s illusts for condition: %s", n, n, cond)

    all_results = []
    for p in db.illust.find(
        cond, {'illustId': 1, 'illustTitle': 1, 'frameInfo': 1, 'bookmarkCount': 1}
    ).sort([('bookmarkCount', pymongo.DESCENDING)]).limit(limit): # 按收藏数降序排列，取前limit张图片
        download_ugoira(p)
        logger.debug("start crawl_anime_file for %s", p['illustId'])
