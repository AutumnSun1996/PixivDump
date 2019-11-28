# PixivDump
Download and analyse illust infomation from pixiv.

Tasks are managed by supervisor and apscheduler.

Crawler is written based on requests-future, using hooks for each request.

Also provide a server by Flask to view illusts and animations downloaded.

The backend database is mongodb, and illusts are saved in GridFS.
