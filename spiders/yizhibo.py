# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
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
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('yizhibo')


class YizhiboSpider(scrapy.Spider):
    name = "yizhibo"
    allowed_domains = ["yizhibo.com"]
    callbacked = None
    video_id = None
    # start_urls = (
    #     'http://www.yizhibo.com/',
    # )
    #

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, live_callback=None, *args, **kwargs):
        super(YizhiboSpider, self).__init__(*args, **kwargs)

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

    def parse(self, response):
        print self.start_urls[0], response.url
        try:
            self.video_id = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        get_info_url = "http://api.xiaoka.tv/live/web/get_play_live?scid=%s" % self.video_id
        resp = requests.get(get_info_url)
        info = resp.json()['data']
        title = info['title']
        cover = 'http://alcdn.img.xiaoka.tv/%s' % info['cover']
        cover = service.utils.get_clip_cover_url(cover, self.uuid)
        url = info['linkurl']

        if info['status'] == 10:
            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'直播',
                "cover": cover,
                "title": title,
                "live_url": url
            }
            self.callbacked = service.utils.callback_result(self.live_callback, data)
            return

        if self.check_db():
            return

        file_path = "cache/%s.mp4" % self.uuid
        length = service.utils.coverterMp4(url, file_path, True)
        # length = service.utils.download_m3u8(url, file_path, self.video_id)
        if length == 0:
            raise CloseSpider('download video failed')
        filesize = os.path.getsize(file_path)
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
            'easub_uuid': self.uuid,
            'cover': cover
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
        yizhibo = r"https?://(www)?\.yizhibo\.com/\w+/(?P<id>[0-9A-Za-z_-]+)"
        weibo = r"https?://(www\.)?weibo\.com/tv/l/(?P<id>[0-9A-Za-z_-]+)"
        m = re.compile(yizhibo).match(url) or re.compile(weibo).match(url)
        assert m
        return m.group('id')

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
            # subprocess.Popen('rm -rf ' + 'cache/' + self.uuid + '*', shell=True)
            service.utils.remove_all_files('cache/', self.uuid)
