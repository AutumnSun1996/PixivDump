# PixivDump
Download and analyse illust infomation from pixiv.

Tasks are managed by supervisor and apscheduler.

Crawler is written based on requests-future, using hooks for each request.

Also provide a server by Flask to view illusts and animations downloaded.

The backend database is mongodb, and illusts are saved in GridFS.


基于 SpiderUtils 重新设计: 

1. 不保存原始图片, 每次均重新从缓存获取或新下载
2. 对部分数据进行预下载缓存
3. 
