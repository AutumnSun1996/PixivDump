import tools as config

from flask import (
    Flask,
    request,
    render_template,
    Response,
    Request,
    jsonify,
    redirect,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import io
from bson import ObjectId
import datetime
import mimetypes
import pymongo
import gridfs
import logging
import json
import cv2 as cv
import numpy as np

import dump

app_host = "0.0.0.0"
app_port = 10101
app_debug = True

if not app_debug:
    from gevent import monkey

    monkey.patch_all()

logger = logging.getLogger("server")

db = dump.db
fs = dump.fs

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def default(obj):
    if isinstance(obj, datetime.datetime):
        return obj.ctime()
    if isinstance(obj, ObjectId):
        return str(obj)
    return obj


def to_json(obj):
    return json.dumps(
        obj, indent=None, ensure_ascii=False, separators=(",", ":"), default=default
    )


@app.route("/pixiv/")
def index():
    return render_template("index.html")


def iter_file(file):
    chunk = file.readchunk()
    while chunk:
        yield chunk
        chunk = file.readchunk()
    print("Streaming finished for", file.metadata)


def mongo_file(file):
    if "ETag" in request.headers and request.headers["ETag"] == file.md5:
        return ("", 304)
    mimetype = mimetypes.guess_type(file.filename)[0]
    data = file.read()
    if (
        "raw" not in request.headers  # 未要求原图
        and file.filename.endswith((".jpg", ".png"))  # 图片文件
        and len(data) > 204800  # 超过200k
    ):
        img = cv.imdecode(np.frombuffer(data, dtype="uint8"), cv.IMREAD_UNCHANGED)
        if img.shape[1] > 1280:
            # 限制最大宽度为 1280
            ratio = 1280 / img.shape[1]
            img = cv.resize(img, (0, 0), fx=ratio, fy=ratio)
        _, res = cv.imencode(".jpg", img, [int(cv.IMWRITE_JPEG_QUALITY), 70])
        logger.info("压缩: %d -> %d", len(data), len(res))
        data = res.tobytes()

    resp = Response(data, mimetype=mimetype)
    resp.cache_control.public = True
    resp.cache_control.max_age = 30 * 24 * 60
    resp.headers["ETag"] = file.md5
    expire = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    resp.headers["Expires"] = expire.strftime("%a, %d %b %Y %H:%M:%S GMT")
    return resp


@app.route("/pixiv/image/<illust_id>/<int:idx>")
def image(illust_id, idx):
    cond = {"metadata.illustId": illust_id, "metadata.pageIndex": idx}
    f = fs.find_one(cond)
    if f is None:
        # 添加下载任务
        logger.info("添加下载任务 download_illust %s", illust_id)
        dump.download_illust(db.illust.find_one({"illustId": illust_id}))
        return "illust not found", 404
    return mongo_file(f)


@app.route("/pixiv/zipFile/<illust_id>")
def zip_image(illust_id):
    cond = {"metadata.illustId": illust_id, "metadata.fileType": "frames"}
    f = fs.find_one(cond)
    if f is None:
        # 添加下载任务
        logger.info("添加下载任务 download_ugoira %s", illust_id)
        dump.download_ugoira(db.illust.find_one({"illustId": illust_id}))
        return "ZipFile not found", 404
    return mongo_file(f)


@app.route("/pixiv/search", methods=["POST"])
def search():
    try:
        match = request.json["match"]
        limit = request.json.get("limit", 100)
    except Exception as e:
        return "Invalid param: " + str(e), 400
    if "$and" in match:
        match["$and"] += [{"detail.error": {"$exists": 0}}]
    else:
        match = {"$and": [match, {"detail.error": {"$exists": 0}}]}
    result = list(
        db.illust.find(match, {"_id": 0, "frameInfo.file": 0},)
        .sort([("bookmarkCount", -1)])
        .limit(limit)
    )
    count = len(result)
    return jsonify({"data": result, "count": count})


@app.route("/pixiv/illust")
def illust():
    return render_template("illust.html")


if __name__ == "__main__":
    
    if app_debug:
        logging.basicConfig(level="DEBUG")
        app.run(app_host, app_port, debug=True)
    else:
        logging.basicConfig(level="INFO")
        from gevent.pywsgi import WSGIServer

        http_server = WSGIServer((app_host, app_port), app, log=logger)
        http_server.serve_forever()
