import urllib3
import logging
import logging.config
import yaml

from configs import *

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.config.dictConfig(yaml.load("""
version: 1
disable_existing_loggers: False
formatters:
    simple:
        format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    dump:
        format: "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s@%(filename)s[%(lineno)d]: %(message)s"
handlers:
    console:
        class: logging.StreamHandler
        level: INFO
        formatter: dump
        stream: ext://sys.stdout
    SimpleConsole:
        class: logging.StreamHandler
        level: INFO
        formatter: simple
        stream: ext://sys.stdout
    main_file:
        class: logging.handlers.RotatingFileHandler
        level: DEBUG
        formatter: dump
        filename: logs/main.log
        maxBytes: 10485760 # 10MB
        backupCount: 10
        encoding: utf8
    error_file:
        class: logging.handlers.RotatingFileHandler
        level: ERROR
        formatter: dump
        filename: logs/errors.log
        maxBytes: 10485760 # 10MB
        backupCount: 10
        encoding: utf8
loggers:
    main:
        level: DEBUG
        handlers: [console, main_file, error_file]
    dump:
        level: DEBUG
        handlers: [console, main_file, error_file]
    server:
        level: DEBUG
        handlers: [SimpleConsole]
root:
    level: INFO
    handlers: [SimpleConsole]
"""))


def parse_header(text, split_by=('\n', ': ')):
    h = {}
    for line in text.strip().split(split_by[0]):
        k, v = line.split(split_by[1], 2)
        h[k] = v
    return h

def check_results(all_results, handle_left=None):
    count = {'total': 0, 'done': 0, 'done_ok':0, 'ok': 0}
    for idx, r in all_results:
        if r.done():
            count['done'] += 1
            if r.exception() is None:
                count['done_ok'] += 1
                if r.result().handler_error is None:
                    count['ok'] += 1
                else:
                    e = r.result().handler_error
                    print(idx)
                    print(e)
                    if handle_left:
                        handle_left(idx, r)
            else:
                print(idx)
                print(r)
                print(r.exception())
                if handle_left:
                    handle_left(idx, r)
        elif handle_left:
            handle_left(idx, r)
        count['total'] += 1
    count['left'] = count['total'] - count['done']
    count['failed'] = count['done'] - count['ok']
    print(count)
    return count

HEADERS = {}
HEADERS['search'] = parse_header("""
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3
Accept-Encoding: gzip, deflate, br
Accept-Language: zh-CN,zh;q=0.9
Cache-Control: no-cache
Connection: keep-alive
Host: www.pixiv.net
Pragma: no-cache
Referer: https://www.pixiv.net/
Sec-Fetch-Mode: navigate
Sec-Fetch-Site: same-origin
Sec-Fetch-User: ?1
Upgrade-Insecure-Requests: 1
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36
""")
HEADERS['search']['Cookie'] = search_cookie

HEADERS['detail'] = parse_header("""
Accept: application/json, text/javascript, */*; q=0.01
Accept-Encoding: gzip, deflate, br
Accept-Language: zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7
Cache-Control: no-cache
Connection: keep-alive
Host: www.pixiv.net
Pragma: no-cache
Referer: https://www.pixiv.net/
Sec-Fetch-Mode: cors
Sec-Fetch-Site: same-origin
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.90 Safari/537.36
X-Requested-With: XMLHttpRequest
""")

key_names = {
    'search': 'illustId illustTitle illustType tags userId userName userImage width height pageCount bookmarkCount'.split(' '),
    'detail': 'tags bookmarkCount pageCount description createDate viewCount likeCount commentCount uploadDate'.split(' '),
    'detail2search': 'illustTitle illustType userId userName bookmarkCount pageCount width height'.split(' '),
    'detail2history': 'illustId bookmarkCount viewCount likeCount commentCount'.split(' '),
}