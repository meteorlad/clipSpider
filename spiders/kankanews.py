# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import string
import time
import re
import requests
import xmltodict
import json
from service.logger import Logger
from scrapy.exceptions import CloseSpider
from service.database import get_database
import service.utils
import os
import subprocess
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('kankanews')


# http://v.kankanews.com/index.php?app=api&mod=public&act=getvideo&id=2442110
# http://www.kankanews.com/a/2016-11-30/0017786457.shtml
class KankanewsSpider(scrapy.Spider):
    name = "kankanews"
    allowed_domains = ["kankanews.com"]
    callbacked = False
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, platform=10901, check_video_url=None, *args, **kwargs):
        super(KankanewsSpider, self).__init__(*args, **kwargs)

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
        self.platform = platform
        self.start_urls.append(url)

    def parse(self, response):
        print 'parsePlayurl', response.url
        try:
            self.video_id = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        if self.check_db():
            return

        title = response.selector.xpath('//title/text()').extract()[0]
        moblile_url = response.selector.xpath('//link[@rel="alternate"]/@href').extract()[0]
        print moblile_url, title
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 5.1.1; Nexus 6 Build/LYZ28E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.23 Mobile Safari/537.36',
            'Referer': self.start_urls[0]
        }
        resp = requests.get(moblile_url, headers=headers)
        content = resp.content
        m = re.compile(r"mp4\s*:\s*\"(?P<mp4>.+)\"").search(content)
        if not m:
            raise CloseSpider('parse failed')
        url = m.group('mp4')
        file_path = 'cache/%s.mp4' % self.uuid
        print url, file_path
        filesize, success = service.utils.download_file(url, file_path)
        if not success:
            raise CloseSpider('download video failed')
        length = service.utils.getVideoLength(file_path)

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj

        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')

        print 'filesize', filesize
        # callback
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
            'author': 'v.qq',
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
                "cover": '',
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

    def match_id(self, url):
        regx = r"https?://.*kankanews\.com/.+/(?P<video_id>\d+)"
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
