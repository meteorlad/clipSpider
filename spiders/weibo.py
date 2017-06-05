# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import json
from service.logger import Logger
from service.database import get_database
import service.utils
import re
from scrapy.exceptions import CloseSpider
import requests
import os
import time
import subprocess
from scrapy import Selector
from urllib import unquote
import service.mail
import sys
import urllib
import base64
import rsa
import binascii
from scrapy.http import Request

reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('weibo')


# http://video.weibo.com/show?fid=1034:d4a82d4c8557db0697da6f6bd29d7ab2
class WeiboSpider(scrapy.Spider):
    name = "weibo"
    allowed_domains = ["weibo.com"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, live_callback=None, *args, **kwargs):
        super(WeiboSpider, self).__init__(*args, **kwargs)

        self.config = ConfigParser.ConfigParser()
        self.config.read("config/config.ini")
        self.uuid = uuid
        self.upload_url = upload_url
        self.callback = callback
        self.check_video_url = check_video_url
        self.live_callback = live_callback
        # initialize db
        with open("config/database.cnf") as f:
            config = json.load(f)
        db_cls = get_database(config.get("database_type", None))
        self.db = db_cls(**config.get("database", {}))
        self.start_urls.append(url)
        self.username = 'liyuzai@126.com'
        self.password = '.0123456789'
        self.prelogin = 'https://login.sina.com.cn/sso/prelogin.php?entry=weibo&rsakt=mod&checkpin=1&client=ssologin.js(v1.4.18)'
        self.login_url = 'https://login.sina.com.cn/sso/login.php?client=ssologin.js(v1.4.18)'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; U; CPU iPhone OS 4_3_2 like Mac OS X; en-us) AppleWebKit/533.17.9 (KHTML, like Gecko) Version/5.0.2 Mobile/8H7 Safari/6533.18.5'}

    def start_requests(self):
        headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.11; rv:48.0) Gecko/20100101 Firefox/48.0',
                'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6,zh-TW;q=0.4',
            }
        self.cookies = self.get_cookie_cache()
        return [Request(self.start_urls[0], callback=self.parse, headers=headers, cookies=self.cookies)]

    def parse(self, response):
        print response.url

        try:
            # headers = {
            #     'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.11; rv:48.0) Gecko/20100101 Firefox/48.0',
            #     'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6,zh-TW;q=0.4',
            # }
            # resp = requests.get(self.start_urls[0], headers=headers, cookies=cookies)
            # content = resp.content
            content = response.body
            self.service_num, self.video_id = self.match_id(response.body)
            # m = re.compile(r"weibo\.com/.+fid=(?P<service_num>\d+):(?P<video_id>[0-9a-zA-Z]+)").search(
            #     self.start_urls[0])
            # if m:
            #     self.service_num = m.group('service_num')
            #     self.video_id = m.group('video_id')
            # else:

        except AssertionError, e:
            raise CloseSpider('link not supported')

        title, cover, url = self.set_live()
        if self.callbacked:
            return

        if self.check_db():
            return

        print self.service_num, self.video_id
        if not url:
            title, url = self.get_origin_video_url(content)
            if not url:
                video_url = 'http://video.weibo.com/show?fid=%s:%s' % (self.service_num, self.video_id)
                print video_url
                resps = requests.get(video_url, cookies=self.cookies, headers={
                    'User-Agent': self.config.get('Preference', 'weibo_mobile_agent'),
                    'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6,zh-TW;q=0.4',
                })
                selector = Selector(text=resps.content, type="html")
                url = selector.xpath('//video/@src').extract()[0]
                title = selector.xpath('//meta[@name="description"]/@content').extract()[0]
            print url, title
        if not url:
            raise CloseSpider('parse weibo failed')
        file_path = "cache/%s.mp4" % self.uuid
        if 'm3u8' in url:
            length = service.utils.coverterMp4("'%s'" % url, file_path, True)
            filesize = os.path.getsize(file_path)
        else:
            filesize, success = service.utils.download_file(url, file_path)
            length = service.utils.getVideoLength(file_path)
            if not success or length < 1:
                raise CloseSpider('download video failed')

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            logger.error('upload video error')
            raise CloseSpider('upload oss failed')

        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": filesize,
            "cover": '',
            "title": title
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': title,
            'video_id': self.video_id,
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': '',
            'easub_uuid': self.uuid
        }
        print video_data
        self.db.save_video(video_data)

    def check_db(self):
        result = service.utils.search_video(video_id=self.video_id, page_url=self.start_urls[0],
                                            check_url=self.check_video_url)
        if result:
            print 'db note url:', self.start_urls[0]
            logger.warn('[db note url]' + self.start_urls[0] + '[uuid]' + self.uuid)
            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": result[2],
                "play_id": result[0],
                "size": result[3],
                "cover": '',
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

    def set_live(self):
        try:
            show_url = 'http://live.weibo.com/show?id=%s:%s' % (self.service_num, self.video_id)
            print show_url
            resp = requests.get(show_url, headers={'User-Agent': self.config.get('Preference', 'weibo_mobile_agent')})
            # print resp.content
            selector = Selector(text=resp.content, type="html")
            replay = selector.xpath('//div[@class="video-box"]/div/text()').extract()[0]
            url = selector.xpath('//div[@class="video-box"]/video/@src').extract()[0]
            cover = selector.xpath('//div[@class="video-box"]/video/@poster').extract()[0]
            title = selector.xpath('//h3[@class="m-text-cut"]/text()').extract()[0]
            print url, cover, title
            if replay == u"回放":
                return title, cover, url
            else:
                data = {
                    "video_id": self.uuid,
                    "state": 1,
                    "message": u'直播',
                    "cover": cover,
                    "title": title,
                    "live_url": url
                }
                self.callbacked = service.utils.callback_result(self.live_callback, data)
                logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)
                return None, None, None
        except Exception, e:
            print e
            return None, None, None

    def get_origin_video_url(self, content):
        match = re.compile(r"video_src=http%3A%2F%2F(?P<video_src>[a-zA-Z0-9%\._~-]+)").search(content)
        if match:
            url = unquote('http%3A%2F%2F' + match.group('video_src'))
            print url
            # if 'miaopai.com' not in url:
            #     return '', None
            selector = Selector(text=content, type="html")
            title = selector.xpath('//title/text()').extract()[0]
            print title
            return title, url
        return '', None

    def match_id(self, body):
        # print 'match_id', body
        selector = Selector(text=body, type="html")
        action_data = selector.xpath('//div[@node-type="common_video_player"]/@action-data').extract()
        if action_data and len(action_data):
            data = action_data[0].strip()
            m = re.compile(r"objectid=(?P<service_num>\d+):(?P<id>[0-9a-zA-Z]+)").search(data)
        else:
            regx = r"service_num=(?P<service_num>\d+)&page_id=(?P<id>[0-9a-zA-Z]+)"
            m = re.compile(regx).search(body) \
                or re.compile(
                r"node-type=\\\"fl_h5_video\\\" action-data=\\\"type=feedvideo&objectid=(?P<service_num>\d+):(?P<id>[0-9a-zA-Z]+)").search(
                body) \
                or re.compile(
                r"action-data=\\\"type=feedlive&objectid=(?P<service_num>\d+):(?P<id>[0-9a-zA-Z]+)").search(body) \
                or re.compile(r"weibo\.com/.+fid=(?P<service_num>\d+):(?P<id>[0-9a-zA-Z]+)").search(self.start_urls[0])
        assert m
        return m.group('service_num'), m.group('id')

    def login(self):
        cookies = self.get_cookie_cache()
        if cookies:
            return cookies
        try:
            resp = requests.get(self.prelogin)
            info = resp.json()
            servertime = info['servertime']
            nonce = info['nonce']
            pubkey = info['pubkey']
            postdata = self.get_post_data(self.username, self.password, nonce, servertime, pubkey)
            info = requests.post(self.login_url + '&' + postdata, headers=self.headers).json()
            print info
            login_url = info['crossDomainUrlList'][0]
            cookies = requests.get(login_url).cookies.get_dict()
            self.set_cookie_cache(cookies)
        except:
            cookies = {}
        return cookies

    def get_cookie_cache(self):
        # cache = open('config/weibo_cookie.json', 'rb').read()
        cookies = self.db.get_cookies('weibo')
        return json.loads(cookies)
        # if not cookies:
        #     return None
        #
        # info = json.loads(cookies)
        # if time.time() < info['timeout']:
        #     print 'cache cookies*****'
        #     return info['cookies']
        # else:
        #     return None

    def set_cookie_cache(self, cookies):
        cache = {
            'cookies': cookies,
            'timeout': int(time.time()) + 12*60*60
        }
        print 'set cookie cache', json.dumps(cache)
        # open('config/weibo_cookie.json', 'wb').write(json.dumps(cache))
        self.db.set_cookies('weibo', json.dumps(cache))


    def get_post_data(self, username, password, nonce, servertime, pubkey):
        su = base64.encodestring(urllib.quote(username))[:-1]
        sign = str(servertime) + '\t' + str(nonce) + '\n' + str(password)
        sp = binascii.b2a_hex(rsa.encrypt(sign, rsa.PublicKey(int(pubkey, 16), 65537)))
        data = {
            'entry': 'account',
            'gateway': '1',
            'from': 'null',
            'savestate': '0',
            'useticket': '0',
            'pagerefer': '',
            'vsnf': '1',
            'su': su,
            'service': 'account',
            'servertime': servertime,
            'nonce': nonce,
            'pwencode': 'rsa2',
            'rsakv': '1330428213',
            'sp': sp,
            'sr': '1366 * 768',
            'encoding': 'UTF-8',
            'cdult': '3',
            'domain': 'sina.com.cn',
            'prelt': '48',
            'returntype': 'TEXT'
        }
        return urllib.urlencode(data)

    def closed(self, reason):
        print '[closed]', reason
        if not self.callbacked:
            data = {
                "video_id": self.uuid,
                "state": 0,
                "message": reason,
                "length": 0,
                "play_id": '',
                "cover": '',
                "title": ''
            }
            service.utils.callback_result(self.callback, data=data)
            service.mail.send_mail('spider failed:' + self.name, self.uuid + ' ' + reason)
        logger.info('[closed reason]' + reason + "[uuid]" + self.uuid)
        if self.video_id:
            service.utils.remove_all_files('cache/', self.uuid)
