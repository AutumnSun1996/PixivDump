import config

from flask import Flask, request, render_template, Response, Request, jsonify, redirect, url_for
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

app_host = '0.0.0.0'
app_port = 10101
app_debug = False

if not app_debug:
    from gevent import monkey
    monkey.patch_all()

logger = logging.getLogger('server')

db = pymongo.MongoClient(**config.mongo_kwargs)[config.mongo_db_name]
fs = gridfs.GridFS(db)

app = Flask(__name__);
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

def default(obj):
    if isinstance(obj, datetime.datetime):
        return obj.ctime()
    if isinstance(obj, ObjectId):
        return str(obj)
    return obj

def to_json(obj):
    return json.dumps(
        obj, indent=None, ensure_ascii=False, 
        separators=(',', ':'),
        default=default
    )

@app.route("/pixiv/")
def index():
    return render_template("index.html")

def iter_file(file):
    chunk = file.readchunk()
    while chunk:
        yield chunk
        chunk = file.readchunk()

def mongo_file(file):
    if 'ETag' in request.headers and request.headers['ETag'] == file.md5:
        return ('', 304)
    print(request.url)
    print(request.headers)
    mimetype = mimetypes.guess_type(file.filename)[0]
    resp = Response(iter_file(file), mimetype=mimetype)
    resp.cache_control.public = True
    resp.cache_control.max_age = 3600
    resp.headers["ETag"] = file.md5
    return resp

@app.route("/pixiv/image/<illust_id>/<int:idx>")
def image(illust_id, idx):
    cond = {'metadata.illustId': illust_id, 'metadata.pageIndex': idx}
    f = fs.find_one(cond)
    if f is None:
        return 'illust not found', 404
    return mongo_file(f)

@app.route("/pixiv/zipFile/<illust_id>")
def zip_image(illust_id):
    cond = {'metadata.illustId': illust_id, 'metadata.fileType': {'$ne': 'illust'}}
    f = fs.find_one(cond)
    if f is None:
        return 'ZipFile not found', 404
    return mongo_file(f)

@app.route("/pixiv/search")
def search():
    try:
        match = json.loads(request.values.get('match'))
        limit = int(request.values.get('limit', 100))
    except Exception as e:
        return 'Invalid param: ' + str(e), 400
    result = list(db.illust.find(
        match, 
        {'_id': 0,}
    ).sort([('bookmarkCount', -1)]).limit(limit))
    return jsonify(result)

@app.route("/pixiv/user/<uid>")
def user_redirect(uid):
    return redirect(url_for('illust', match=to_json({'userId': uid})))

@app.route("/pixiv/illust")
def illust():
    try:
        match = json.loads(request.values.get('match'))
        limit = int(request.values.get('limit', 100))
        idx = int(request.values.get('idx', 0))
    except Exception as e:
        return 'Invalid param: ' + str(e), 400
    result = list(db.illust.find(
        match, 
        {'_id': 0}
    ).sort([('bookmarkCount', -1)]).limit(limit))
    return render_template("illust.html", illust=to_json(result), idx=idx, count=len(result))

if __name__ == "__main__":
    if app_debug:
        app.run(app_host, app_port, debug=True)
    else:
        from gevent.pywsgi import WSGIServer
        http_server = WSGIServer((app_host, app_port), app, log=logger)
        http_server.serve_forever()
