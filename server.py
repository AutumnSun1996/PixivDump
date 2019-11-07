from flask import Flask, request, render_template, Response, jsonify, redirect, url_for

import json
import io
from bson import ObjectId
import datetime
import mimetypes
import pymongo
import gridfs
import logging
import json

import config
logger = logging.getLogger(__name__)

db = pymongo.MongoClient(**config.mongo_kwargs)[config.mongo_db_name]
fs = gridfs.GridFS(db)


app = Flask(__name__);

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


@app.route("/pixiv/image")
def image():
    illust_id = request.values.get('illustId')
    if illust_id is None:
        return 'Need illustId', 400
    idx = int(request.values.get('pageIndex', 0))
    cond = {'metadata.illustId': illust_id, 'metadata.pageIndex': idx}
    f = fs.find_one(cond)
    print(cond, f)
    if f is None:
        return 'illust not found', 404
    mimetype = mimetypes.guess_type(f.filename)[0]
    return Response(f.read(), mimetype=mimetype)

@app.route("/pixiv/zipFile")
def zip_image():
    illust_id = request.values.get('illustId')
    if illust_id is None:
        return 'Need illustId', 400
    cond = {'metadata.illustId': illust_id, 'metadata.fileType': {'$ne': 'illust'}}
    f = fs.find_one(cond)
    if f is None:
        return 'ZipFile not found', 404
    mimetype = mimetypes.guess_type(f.filename)[0]
    return Response(f.read(), mimetype=mimetype)

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
    print(result)
    return render_template("illust.html", illust=to_json(result), idx=idx, count=len(result))

if __name__ == "__main__":
    if True:
        app.run('0.0.0.0', 10101, debug=True)
    else:
        from gevent import monkey
        monkey.patch_all()
        from gevent.pywsgi import WSGIServer
        http_server = WSGIServer(('0.0.0.0', 10101), app)
        http_server.serve_forever()
