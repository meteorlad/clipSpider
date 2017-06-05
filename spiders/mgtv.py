# -*- coding: utf-8 -*-
import youtube_dl
import scrapy
import ConfigParser
import json
import string
import os
import subprocess
import time
from service.logger import Logger
from service.database import get_database
import service.utils
import re
import requests
from scrapy.exceptions import CloseSpider
import sys
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('mgtv')


class MgtvSpider(scrapy.Spider):
    name = "mgtv"
    allowed_domains = ["mgtv.com"]
    callbacked = None
    video_id = None
    clip_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(MgtvSpider, self).__init__(*args, **kwargs)

        self.config = ConfigParser.ConfigParser()
        self.config.read("config/config.ini")
        self.uuid = uuid
        self.upload_url = upload_url
        self.callback = callback
        self.check_video_url = check_video_url
        # initialize db
        with open("config/database.cnf") as f:
            config = json.load(f)
        db_cls = get_database(config.get("database_type", None))
        self.db = db_cls(**config.get("database", {}))
        self.start_urls.append(url)

    def parse(self, response):
        # http://www.mgtv.com/b/309238/3761304.html
        print response.url

        try:
            self.video_id, self.clip_id = self.match_id(response.url)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        url, title, pic = self.merge_url(self.video_id)
        print title, pic, url
        file_path = "cache/%s.mp4" % self.uuid
        length = service.utils.download_m3u8(url, file_path, self.uuid, False)

        if length < 1:
            raise CloseSpider('download video failed')
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            logger.error('upload video error')
            raise CloseSpider('upload oss failed')

        cover = service.utils.get_clip_cover_url(pic, self.uuid)
        filesize = os.path.getsize(file_path)
        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": filesize,
            "cover": cover,
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
            'video_url': url,
            'easub_uuid': self.uuid
        }
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
                "cover": result[4],
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

    def match_id(self, url):
        # translate /l/listid to be /b/video_id
        patten = r'http://(?:www.)mgtv.com/l/(\d+?).html'
        items = re.findall(patten, url)
        print items
        if items:
            list_id = items[0]
            if list_id != '':
                translate_url = 'http://pcweb.api.mgtv.com/listplay/list?callback=jQuery18205363378300721668_1484553919161&list_id=' + list_id + '&page=1&size=6&video_id=0&_=1484553919357'
                translatetext = requests.get(translate_url).text
                patten = '"video_id":"(\d+)"'
                items = re.findall(patten, translatetext)
                bid = items[0]
                url = 'http://www.mgtv.com/b/1/' + bid + '.html'

        regx = r'http://(?:www.)mgtv.com/\w/(?P<clip>\d+)/(?P<id>\d+)'
        m = re.compile(regx).match(url)
        assert m
        return m.group('id'), m.group('clip')

    def merge_url(self, video_id):
        url1 = "http://pcweb.api.mgtv.com/player/video?video_id=" + video_id
        info = requests.get(url1).json()
        title = info['data']['info']['title']
        pic = info['data']['info']['thumb']
        if len(info['data']['stream']) > 1:
            mergeurl = info['data']['stream_domain'][0] + info['data']['stream'][1]['url']
        else:
            mergeurl = info['data']['stream_domain'][0] + info['data']['stream'][0]['url']
        url2 = mergeurl
        secondtext = requests.get(url2)
        secondtext.encoding = 'utf-8'
        secondtext = secondtext.text
        patten = '"info":"(.*?)"'
        items = re.findall(patten, secondtext)
        mergeurl = items[0]
        return mergeurl, title, pic

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
