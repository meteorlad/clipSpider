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
import service.video_add_subtitle
import subprocess
import os
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('open.163')


class Open163Spider(scrapy.Spider):
    name = "open.163"
    allowed_domains = ["open.163.com", "v.163.com"]
    callbacked = None
    video_id = None

    # start_urls = (
    #     'http://www.open.163.com/',
    # )
    #

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(Open163Spider, self).__init__(*args, **kwargs)

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
            plid, mid = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        self.video_id = mid
        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + mid)
        if self.check_db():
            return

        if 'v.163.com' in self.start_urls[0]:
            title = response.selector.xpath('//meta[@name="description"]/@content').extract()[0].strip()
            m = re.search(r'<source src="(?P<video_url>\S+)"', response.body)
            print m.group()
            video_url = m.group('video_url')
            print title, video_url
        else:
            info_url = "http://so.open.163.com/movie/%s/getMovies4Ipad.htm" % plid
            resp = requests.get(info_url)
            playlist = resp.json()
            videos = playlist['videoList']
            info = None
            for video in videos:
                if video['mid'] == mid:
                    info = video
                    break

            title = info['title']
            video_url = info['repovideourlmp4']

        tmp_path = 'cache/' + self.uuid + '.mp4'
        filesize, success = service.utils.download_file(video_url, tmp_path)
        if not success:
            raise CloseSpider('download video failed')

        length = service.utils.getVideoLength(tmp_path)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        filesize = os.path.getsize(tmp_path)
        result = service.utils.uploadVideo(tmp_path, endpoint, backet, obj)
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
            'video_id': mid,
            'author': self.name,
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
                "cover": '',
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

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

    def match_id(self, url):
        regx = r"https?://open\.163\.com/movie/([0-9A-Z/]+)/(?P<plid>[0-9A-Za-z]+)_(?P<mid>[0-9A-Za-z]+)"
        v_163 = r"https?://v\.163\.com/([0-9A-Za-z/]+)/(?P<plid>[0-9A-Za-z]+)/(?P<mid>[0-9A-Za-z]+)"
        m = re.compile(regx).match(url) or re.compile(v_163).match(url)
        assert m
        return m.group('plid'), m.group('mid')
