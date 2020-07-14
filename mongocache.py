from spider_utils.cache import CacheManager, ResponseSerializer
import pymongo
import requests
import gridfs

import tools as config


class MongoFSCache(CacheManager):
    def __init__(self):
        db = pymongo.MongoClient(**config.mongo_kwargs)[config.mongo_db_name]
        self.fs = gridfs.GridFS(db)

    @staticmethod
    def dumps_key(method, url, kwargs):
        """根据请求信息生成请求key. 将作为Cache的查找依据
        """
        if not url.endswith((".jpg", ".png", ".zip")):
            return {"ignore": True}
        info = {'illustId': pid, 'pageIndex': index, 'fileType': 'illust'}
        return {"url": url}

    def get(self, key, default=None, expire=-1):
        """获取缓存项

        :param key: 缓存关键词
        :param default: 无结果时的返回值
        :param expire: 超时时间, 为0时不会进行查找
            其他情况下MongoFSCache将始终认为缓存有效
        """
        logger.debug("查找缓存 %s %.3f", key, expire)
        if expire == 0:
            logger.debug("强制超时: %s", key)
            return default
        res = self.fs.find_one(key)
        if res is None:
            logger.debug("无缓存: %s", key)
            # 缓存未命中, 返回默认值
            return default
        logger.debug("缓存命中: %s", key)
        resp = requests.Response()
        resp._content = res.read()
        resp.meta = res.metadata
        resp.url = res.filename
        return resp

    def __setitem__(self, key, response):
        if "ignore" in key:
            return
        file_id = fs.put(
            filename=key["url"], data=response.content, metadata=key["info"]
        )
