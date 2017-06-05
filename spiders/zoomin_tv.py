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
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('zoomin.tv')


class ZoominTvSpider(scrapy.Spider):
    name = "zoomin.tv"
    allowed_domains = ["zoomin.tv"]
    callbacked = False
    pids = ['corporateusahddp', 'corporateuk', 'corporateke', 'corporatees', 'corporatelatamdp', 'corporatecataldp',
            'corporatenl',
            'corporatevla', 'corporatede', 'corporateit', 'corporatefr', 'corporatewal', 'corporatebradp',
            'corporatetr', 'corporateswedp',
            'corporateru', 'corporatejp', 'corporatechinacndp', 'corporatearabdp']

    # start_urls = (
    #     'http://www.zoomin.tv/',
    # )
    # http://blackbird.zoomin.tv/ProgramXml/.json?feedtype=json&pid=corporateusahddp&vtype=direct&aid=754116
    # http://zoomin.tv/video/#!v/754116/

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(ZoominTvSpider, self).__init__(*args, **kwargs)
        print 'init', url
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
        print 'parsePlayurl', response.url
        try:
            video_id = self._match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + video_id)
        if self.check_db():
            return

        video = None
        for pid in self.pids:
            getinfo_url = 'http://blackbird.zoomin.tv/ProgramXml/.json?feedtype=json&pid=%s&vtype=direct&aid=%s' % (
                pid, video_id)
            resp = requests.get(getinfo_url)
            info = resp.json()
            print info
            if len(info['programme']) > 0:
                video = info['programme'][0]
                break

        video_url = video['videourl']
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideoByUrl(video_url, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')

        filesize = video['videosize']
        length = int(video['videoduration']) / 1000.0
        title = video['title']
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
            'video_id': video_id,
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

    def _match_id(self, url):
        zoomin = r'https?://(?:www\.)?zoomin\.tv/video/#\!v/(?P<id>[\d]{6})'
        m = re.compile(zoomin).match(url)
        assert m
        return m.group('id')
