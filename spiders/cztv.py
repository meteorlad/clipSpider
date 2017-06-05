# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import time
import re
import requests
import json
from service.logger import Logger
from scrapy.exceptions import CloseSpider
from service.database import get_database
import service.utils
import os
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('cztv')


class CztvSpider(scrapy.Spider):
    name = "cztv"
    callbacked = False
    video_id = None
    times = 0

    def __init__(self, url, uuid, upload_url, callback, platform=11001, check_video_url=None, live_callback=None, *args,
                 **kwargs):
        super(CztvSpider, self).__init__(*args, **kwargs)

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
        self.platform = platform
        self.start_urls.append(url)

    def parse(self, response):
        print response.url
        try:
            self.video_id = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        if self.check_db():
            return

        url = 'http://api.cms.cztv.com/mms/out/video/playJson?id=%s&platid=111&splatid=1002&format=1&pt=4&at=1&domain=tv.cztv.com' % self.video_id
        info = requests.get(url).json()
        print info
        title = info['playurl']['title']
        duration = info['playurl']['duration']
        pic = info['playurl']['pic']
        video_url = info['playurl']['dispatch'][0]['url'][0].values()[0]
        print 'video_url', video_url
        file_path = 'cache/%s.mp4' % self.uuid
        length = service.utils.download_m3u8(video_url, file_path, self.uuid, False)
        if length < 1:
            raise CloseSpider('download video failed')

        cover = service.utils.get_clip_cover_url(pic, self.uuid)

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj

        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')

        filesize = os.path.getsize(file_path)
        print 'filesize', filesize
        # callback
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
            'author': 'v.qq',
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': video_url,
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
        regx = r"https?://(.*\.)?cztv\.com/vplay/(?P<video_id>\d+).html"
        m = re.compile(regx).match(url)
        assert m
        return m.group('video_id')

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
