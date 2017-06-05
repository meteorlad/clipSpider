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
import base64
import time
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('1905')


class A1905Spider(scrapy.Spider):
    name = "1905"
    allowed_domains = ["1905.com"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(A1905Spider, self).__init__(*args, **kwargs)

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
        print response.url

        try:
            self.video_id = self.match_id(response.url)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        info_url = "http://www.1905.com/api/video/getmediainfo.php?jsoncallback=&id=%s&type=1&fr=www&source_key=mcfile" % self.video_id
        print info_url
        resp = requests.get(info_url, headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/56.0.2924.87 Mobile Safari/537.36"
        })
        print resp.content
        info = json.loads(resp.content[1:-1])
        cover = service.utils.get_clip_cover_url(info['thumb'], self.uuid)
        url = base64.decodestring(info['iosurl'])
        file_path = 'cache/%s.mp4' % self.uuid
        file_size, success = service.utils.download_file(url, file_path)
        if not success:
            raise CloseSpider('download failed')
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not uploadResult:
            raise CloseSpider('upload oss failed')
        duration = info['duration']
        title = response.xpath('//title/text()').extract()[0]
        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": duration,
            "play_id": self.uuid,
            "size": file_size,
            "cover": cover,
            "title": title
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': title,
            'video_id': self.video_id,
            'author': '1905',
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': duration,
            'video_size': file_size,
            'video_url': '',
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
        regx = r"https?://www.1905.com/.+/(?P<id>\d+)"
        m = re.compile(regx).match(url)
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
            service.utils.remove_all_files('cache/', self.uuid)
