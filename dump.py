import re
import json
import math
import json
import datetime
from urllib.parse import quote_plus
from functools import partial

import pymongo
import gridfs
import pyquery
from requests_futures.sessions import FuturesSession
import logging

import tools as config

logger = logging.getLogger(__name__)

db = pymongo.MongoClient(**config.mongo_kwargs)[config.mongo_db_name]
fs = gridfs.GridFS(db)

session = FuturesSession(adapter_kwargs={"max_retries": 3})

task_statistics = {"total": 0, "done": 0, "success": 0}


def update_result(r, target, *args, **kwargs):
    if not hasattr(r, "handler_error"):
        return
    target["done"] += 1
    if r.handler_error is None:
        target["success"] += 1


def send_request(method, url, **kwargs):
    target = task_statistics  # make a ref of task_statistics, in case it reset between the exec of following two lines
    target["total"] += 1
    hook_func = partial(update_result, target=target)
    # update hooks, set 'update_result' as the last hook
    if "hooks" not in kwargs:
        kwargs["hooks"] = {}
    if "response" not in kwargs["hooks"]:
        kwargs["hooks"]["response"] = [hook_func]
    elif callable(kwargs["hooks"]["response"]):
        kwargs["hooks"]["response"] = [kwargs["hooks"]["response"], hook_func]
    elif isinstance(kwargs["hooks"]["response"], (list, tuple)):
        hooks = []
        hooks.extend(kwargs["hooks"]["response"])
        hooks.append(hook_func)
        kwargs["hooks"]["response"] = hooks
    return session.request(method, url, **kwargs)


def get_statistics():
    info = task_statistics.copy()
    info["failed"] = info["done"] - info["success"]
    info["running"] = info["total"] - info["done"]
    return info


def show_statistics():
    logger.warning("current statistics: %s", get_statistics())


def reset_statistics():
    global task_statistics
    logger.warning("last statistics: %s", get_statistics())
    task_statistics = {"total": 0, "done": 0, "success": 0}


def update_search(response, *args, **kwargs):
    """处理搜索结果"""
    items = None
    try:
        info = response.json()["body"]
        items = []
        for key in ["illustManga", "illust", "manga"]:
            if key in info:
                items += info[key]["data"]
        items += info["popular"]["recent"] + info["popular"]["permanent"]
        for origin_item in items:
            if origin_item.get("illustId", None) is None:
                continue
            item = {}
            for key in config.key_names["search"]:
                item[key] = origin_item[key]
            item["updateTime"] = datetime.datetime.now()
            # 保证illustType为int类型
            if "illustType" in item:
                item["illustType"] = int(item["illustType"])
            try:
                db.illust.insert_one(item)
                logger.debug("insert, illustId=%s", item.get("illustId"))
            except pymongo.errors.DuplicateKeyError:
                item.pop("_id")
                pid = item.pop("illustId")
                db.illust.update_one({"illustId": pid}, {"$set": item})
                logger.debug("update, illustId=%s", pid)
        response.handler_error = None
    except Exception as e:
        response.handler_error = e
        logger.exception(
            "update_search failed for %s => %s: %s", response.url, items, e
        )


def try_update_illust(illust, update_time):
    try:
        item = {}
        for key in config.key_names["search"]:
            if key in illust:
                item[key] = illust[key]
        # 保证illustType为int类型
        if "illustType" in item:
            item["illustType"] = int(item["illustType"])
        item["updateTime"] = update_time
        db.illust.update_one(
            {"illustId": illust["illustId"]}, {"$set": item}, upsert=True
        )
    except Exception as e:
        logger.exception("try_update_illust failed for %s: %s", illust, e)


def update_detail(response, pid, *args, **kwargs):
    """处理详情信息"""
    try:
        now = datetime.datetime.now()
        item = {"detail": {}, "updateTime": now}
        history = {"updateTime": now}
        info = response.json()
        if info["error"]:
            if not info["body"]:
                info.pop("body")
            item.pop("detail")
            item["detail.error"] = info
        else:
            origin_item = info["body"]
            for key in config.key_names["detail"]:
                item["detail"][key] = origin_item[key]
            for key in config.key_names["detail2search"]:
                item[key] = origin_item[key]
            for key in config.key_names["detail2history"]:
                history[key] = origin_item[key]
            # 更新 tags
            item["tags"] = [t["tag"] for t in origin_item["tags"]["tags"]]
            url = origin_item["urls"]["original"]
            idx = url.rfind(".")
            item["imageUrlFormat"] = url[: idx - 1] + "{pageIndex}" + url[idx:]
            # 保证illustType为int类型
            if "illustType" in item:
                item["illustType"] = int(item["illustType"])

            # 尝试根据 userIllusts 更新其他插画的信息
            for other in origin_item.get("userIllusts", {}).values():
                if other is None or pid != other["illustId"]:
                    continue
                try_update_illust(other, item["updateTime"])

        db.illust.update_one({"illustId": pid}, {"$set": item}, upsert=True)
        db.illustHistory.insert_one(history)
        response.handler_error = None
    except Exception as e:
        response.handler_error = e
        logger.exception("update_detail failed for %s(%s): %s", pid, response.url, e)


def update_ugoira_meta(response, pid, *args, **kwargs):
    """处理动画信息"""
    try:
        logger.debug("update_ugoira_meta for %s start", pid)
        item = {}
        info = response.json()
        if info["error"]:
            if not info["body"]:
                info.pop("body")
            item["frameInfo"] = info
        else:
            item["frameInfo"] = info["body"]
        item["frameInfo"]["updateTime"] = datetime.datetime.now()
        db.illust.update_one({"illustId": pid}, {"$set": item})
        response.handler_error = None
        logger.debug("update_ugoira_meta for %s succeed", pid)
    except Exception as e:
        response.handler_error = e
        logger.exception(
            "update_ugoira_meta failed for %s(%s): %s", pid, response.url, e
        )


def save_illust(response, pid, index, **kwargs):
    """保存文件"""
    try:
        if response.status_code != 200:
            raise Exception("HTTP %d" % response.status_code)

        logger.debug("save_illust for %s start", pid)
        info = {"illustId": pid, "pageIndex": index, "fileType": "illust"}
        url = response.url

        if fs.exists(filename=url):
            file_id = fs.get_last_version(filename=url)._id
            logger.warning("skip existing file: %s(%s)", pid, url)
        else:
            file_id = fs.put(filename=url, data=response.content, metadata=info)

        count = db.fs.files.count_documents(
            {"metadata.illustId": pid, "metadata.fileType": "illust"}
        )
        res = db.illust.update_one({"illustId": pid}, {"$set": {"fileCount": count}})
        if not res.raw_result["n"]:
            logger.warning(
                "save_illust for %s(%s) update result: %s", pid, url, res.raw_result
            )

        logger.debug("save_illust for %s succeed", pid)
        response.handler_error = None
    except Exception as e:
        response.handler_error = e
        logger.exception("save_illust failed for %s(%s): %s", pid, response.url, e)


def save_file(response, pid, **kwargs):
    """保存文件"""
    try:
        logger.debug("save_file for %s start", pid)
        info = {"illustId": pid, "fileType": "frames"}
        url = response.url
        if fs.exists(filename=url):
            file_id = fs.get_last_version(filename=url)._id
            logger.warning("ignore existing file: %s(%s)", pid, url)
        else:
            file_id = fs.put(filename=url, data=response.content, metadata=info)

        info = {"filename": url, "fileId": file_id}
        res = db.illust.update_one(
            {"illustId": pid}, {"$set": {"frameInfo.file": info}}
        )
        response.handler_error = None
        logger.debug("save_file for %s succeed", pid)
    except Exception as e:
        response.handler_error = e
        logger.exception("save_file failed for %s(%s): %s", pid, response.url, e)


def count_local_illust(params):
    compare_cond = [{"detail.error": {"$exists": 0}}]
    if params.get("s_mode") is None:
        compare_cond += [{"tags": {"$regex": params["word"]}}]
    elif params["s_mode"] == "s_tag_full":
        compare_cond += [{"tags": {"$eq": params["word"]}}]
    elif params["s_mode"] == "s_tc":
        compare_cond += [{"content": {"$regex": params["word"]}}]
    else:
        logger.error("invalid s_mode: %s", params["s_mode"])

    if params.get("type", "all") != "all":
        local_key = {"illust": 0, "manga": 1, "ugoira": 2}[params["type"]]
        compare_cond += [{"illustType": {"$eq": local_key}}]

    if params.get("mode") is not None:
        mode = params["mode"].lower()
        if mode == "r18":
            compare_cond += [{"tags": {"$eq": "R-18"}}]
        elif mode == "safe":
            compare_cond += [{"tags": {"$ne": "R-18"}}]

    if params.get("wlt") is not None:
        compare_cond += [{"width": {"$gte": params["wlt"]}}]
    if params.get("wgt") is not None:
        compare_cond += [{"width": {"$lte": params["wgt"]}}]
    if params.get("hlt") is not None:
        compare_cond += [{"height": {"$gte": params["hlt"]}}]
    if params.get("hgt") is not None:
        compare_cond += [{"height": {"$lte": params["hgt"]}}]

    if params.get("scd") is not None:
        compare_cond += [{"detail.createDate": {"$gte": params["scd"]}}]
    if params.get("ecd") is not None:
        # '~' = chr(126), 为可见ascii字符最大
        compare_cond += [{"detail.createDate": {"$lte": params["ecd"] + "~"}}]

    local_count = db.illust.count_documents({"$and": compare_cond})
    logger.info("found %s pictures in database for cond %s", local_count, compare_cond)
    return local_count


def crawl_by_search(search_params, skip_exists=True, page_limit=100, use_scd=None):
    # 第一页
    logger.debug("start crawl_by_search of fisrt page for %s", search_params)
    params = config.default_search_params.copy()
    params.update(search_params)
    search_url = "https://210.140.131.182:443/ajax/search/artworks/" + quote_plus(
        params["word"]
    )
    future = send_request(
        "GET",
        search_url,
        params=params,
        timeout=config.TIMEOUT,
        verify=False,
        headers=config.HEADERS["search"],
        hooks={"response": update_search,},
    )
    res = future.result()
    info = res.json()["body"]["illustManga"]
    n_pics = info["total"]
    n_per_page = len(info["data"])
    if n_per_page == 0:
        logger.info("Get no pictures(total=%d) for %s, abort.", n_pics, search_params)
        return
    n_pages = math.ceil(n_pics / n_per_page)
    logger.info("found %s pictures in %s pages for %s", n_pics, n_pages, search_params)
    if use_scd is not None:
        if isinstance(use_scd, (int, float)):
            use_scd = datetime.timedelta(days=use_scd)
        elif isinstance(use_scd, (tuple, list)):
            use_scd = datetime.timedelta(*use_scd)
        scd = datetime.datetime.now() - use_scd
        params["scd"] = scd.strftime("%Y-%m-%d")

    if skip_exists:
        n_pics_more = n_pics - count_local_illust(params)
    else:
        n_pics_more = n_pics

    n_pages_more = min(math.ceil(n_pics_more / n_per_page), page_limit, 1000) - 1
    if n_pages_more < 0:
        n_pages_more = 0
    n_pics_more = min(n_pics_more, n_pages_more * n_per_page)
    logger.info("will crawl %s pages for %s pictures", n_pages_more, n_pics_more)

    # 其余页
    for i in range(2, n_pages_more + 2):
        p = params.copy()
        p["p"] = i
        r = send_request(
            "GET",
            search_url,
            params=p,
            timeout=config.TIMEOUT,
            verify=False,
            headers=config.HEADERS["search"],
            hooks={"response": update_search,},
        )
        logger.debug("start crawl_by_search for %s", p)


def crawl_detail_by_id(illust_id):
    header = config.HEADERS["detail"].copy()
    header["Referer"] = "https://www.pixiv.net/artworks/" + illust_id
    r = send_request(
        "GET",
        "https://210.140.131.182:443/ajax/illust/" + illust_id,
        timeout=config.TIMEOUT,
        verify=False,
        headers=header,
        hooks={"response": partial(update_detail, pid=illust_id),},
    )
    logger.debug("start crawl_detail for %s", illust_id)
    return r


def crawl_detail(limit=1000):
    cond = {"detail": {"$exists": 0}}
    n = db.illust.count_documents(cond)
    logger.info("crawl_detail of %s in %s illusts for condition: %s", limit, n, cond)

    for p in (
        db.illust.find(cond, {"_id": 0, "illustId": 1, "bookmarkCount": 1})
        .sort("bookmarkCount", pymongo.DESCENDING)
        .limit(limit)
    ):
        crawl_detail_by_id(p["illustId"])
    if n >= limit:
        return
    limit -= n
    now = datetime.datetime.now()
    today = datetime.datetime(now.year, now.month, now.day)
    cond = {"updateTime": {"$lt": today}, "detail.error": {"$ne": True}}
    n = db.illust.count_documents(cond)
    logger.info("crawl_detail of %s in %s illusts for condition: %s", limit, n, cond)
    for p in (
        db.illust.find(cond, {"_id": 0, "illustId": 1})
        .sort("updateTime", pymongo.ASCENDING)
        .limit(limit)
    ):
        crawl_detail_by_id(p["illustId"])


def crawl_anime_info():
    # 将动画信息更新到frameInfo
    cond = {"frameInfo": {"$exists": 0}, "illustType": 2}
    n = db.illust.count_documents(cond)
    logger.info("crawl_anime_info of %s in %s illusts for condition: %s", n, n, cond)

    for p in db.illust.find(cond, {"_id": 0, "illustId": 1}).sort(
        "bookmarkCount", pymongo.DESCENDING
    ):
        pid = p["illustId"]
        header = config.HEADERS["detail"].copy()
        header["Referer"] = "https://www.pixiv.net/artworks/{}".format(pid)
        r = send_request(
            "GET",
            "https://210.140.131.182:443/ajax/illust/{}/ugoira_meta".format(pid),
            timeout=config.TIMEOUT,
            verify=False,
            headers=header,
            hooks={"response": partial(update_ugoira_meta, pid=pid),},
        )
        logger.debug("start crawl_anime_info for %s", pid)


def download_illust(p):
    count = db.fs.files.count_documents(
        {"metadata.illustId": p["illustId"], "metadata.fileType": "illust"}
    )
    db.illust.update_one({"illustId": p["illustId"]}, {"$set": {"fileCount": count}})
    if count == p["pageCount"]:
        logger.info("download_illust: %s的数量已更新(%d), 无需下载", p["illustId"], count)
        return

    for i in range(p["pageCount"]):
        url = p["imageUrlFormat"].format(pageIndex=i)
        if fs.exists(filename=url):
            logger.debug("skip download_illust(exists): %s", url)
            continue

        hds = {
            "Sec-Fetch-Mode": "navigate",
            "Referer": "https://www.pixiv.net/artworks/" + p["illustId"],
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.132 Safari/537.36",
        }
        if "i.pximg.net" in url:
            url = url.replace("i.pximg.net", "210.140.92.140:443")
            hds["Host"] = "i.pximg.net"
        r = send_request(
            "GET",
            url,
            timeout=config.TIMEOUT,
            verify=False,
            headers=hds,
            hooks={"response": partial(save_illust, pid=p["illustId"], index=i),},
        )
        logger.debug("start download_illust at %s", url)


def download_ugoira(p):
    url = p["frameInfo"]["originalSrc"]
    if fs.exists(filename=url):
        return
    r = send_request(
        "GET",
        url.replace("i.pximg.net", "210.140.92.140:443"),
        timeout=config.TIMEOUT,
        verify=False,
        headers={
            "Sec-Fetch-Mode": "no-cors",
            "Host": "i.pximg.net",
            "Referer": "https://www.pixiv.net/artworks/" + p["illustId"],
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90",
        },
        hooks={"response": partial(save_file, pid=p["illustId"]),},
    )
    logger.debug("start download_ugoira at %s", url)


def crawl_illust_file(limit=100):
    # 下载图片
    n = list(
        db.illust.aggregate(
            [
                {
                    "$match": {
                        "bookmarkCount": {"$gte": config.illust_min_bookmarks},
                        "detail.error": {"$exists": 0},
                    }
                },
                {"$addFields": {"countMatch": {"$eq": ["$pageCount", "$fileCount"]}}},
                {"$match": {"countMatch": False}},
                {"$count": "n"},
            ]
        )
    )
    if n:
        n = n[0]["n"]
    else:
        # no matched illust
        n = 0
    logger.info("crawl_illust_file of %s in %s illusts", limit, n)
    for p in db.illust.aggregate(
        [
            {
                "$match": {
                    "bookmarkCount": {"$gte": config.illust_min_bookmarks},
                    "detail.error": {"$exists": 0},
                }
            },
            {"$addFields": {"countMatch": {"$eq": ["$pageCount", "$fileCount"]}}},
            {"$match": {"countMatch": False}},
            {
                "$project": {
                    "illustId": 1,
                    "illustTitle": 1,
                    "imageUrlFormat": 1,
                    "pageCount": 1,
                    "bookmarkCount": 1,
                }
            },
            {"$sort": {"bookmarkCount": -1}},  # 按收藏数降序排列
            {"$limit": limit},  # 取前limit张图片
        ]
    ):
        logger.debug("start crawl_illust for %s", p["illustId"])
        download_illust(p)


def crawl_anime_file(limit=100):
    cond = {
        "bookmarkCount": {"$gte": config.anime_min_bookmarks},
        "frameInfo.originalSrc": {"$exists": 1},
        "frameInfo.file": {"$exists": 0},
    }
    n = db.illust.count_documents(cond)
    logger.info("crawl_anime_file of %s in %s illusts for condition: %s", n, n, cond)

    all_results = []
    for p in (
        db.illust.find(
            cond, {"illustId": 1, "illustTitle": 1, "frameInfo": 1, "bookmarkCount": 1}
        )
        .sort([("bookmarkCount", pymongo.DESCENDING)])
        .limit(limit)
    ):  # 按收藏数降序排列，取前limit张图片
        download_ugoira(p)
        logger.debug("start crawl_anime_file for %s", p["illustId"])

