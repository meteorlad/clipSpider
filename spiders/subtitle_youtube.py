# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
import youtube_dl
import scrapy
import ConfigParser
import json
import os
from service.logger import Logger
from service.database import get_database
import service.utils
import re
from scrapy.exceptions import CloseSpider
from scrapy import Selector
import subprocess
import string
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('subtitle.youtube')


class SubtitleYoutubeSpider(scrapy.Spider):
    name = "subtitle.youtube"
    allowed_domains = ["youtube.com"]
    callbacked = None
    auto = 0
    insub = None

    # start_urls = (
    #     'https://www.youtube.com/watch?v=wK222wGwrZg&list=PL2HEDIx6Li8j_jT8JI90WmXeQ657UUKSH&index=35',
    # )

    def __init__(self, url, callback, sub='vtt', lang='en', upload_url=None, *args, **kwargs):
        super(SubtitleYoutubeSpider, self).__init__(*args, **kwargs)

        self.start_urls.append(url)
        self.config = ConfigParser.ConfigParser()
        self.config.read("config/config.ini")
        self.sub = sub
        self.lang = lang
        self.callback = callback
        self.upload_url = upload_url
        # initialize db
        with open("config/database.cnf") as f:
            config = json.load(f)
        db_cls = get_database(config.get("database_type", None))
        self.db = db_cls(**config.get("database", {}))

    def parse(self, response):
        print response.url

        try:
            self.video_id = self._match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        self.start_urls[0] = 'https://www.youtube.com/watch?v=' + self.video_id

        logger.warn('[parse]' + self.start_urls[0] + ' [video_id]' + self.video_id)

        if 'zh' in self.lang:
            self.lang = 'zh-Hans'

        ydl_opts = {
            'skip_download': True,
            'outtmpl': 'cache/%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'writesubtitles': True,
            # 'writeautomaticsub': True,
            # 'subtitleslangs': [self.lang],
            'logger': self
        }

        print ydl_opts

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download(self.start_urls)

        if not self.insub:
            self.auto = 1
            ydl_opts = {
                'skip_download': True,
                'outtmpl': 'cache/%(id)s.%(ext)s',
                'subtitlesformat': 'ttml',
                'writeautomaticsub': True,
                'subtitleslangs': [self.lang],
                'logger': self
            }

            print ydl_opts

            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                ydl.download(self.start_urls)

        if not self.insub or not os.path.exists(self.insub):
            raise CloseSpider('subtitle not exist')
        if self.ext == 'ttml':
            self.covert_ttml()
        outsub = 'cache/%s.%s' % (self.video_id, self.sub)
        command = 'ffmpeg -i %s %s' % (self.insub, outsub)
        print command
        os.system(command)
        sub_path = outsub
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(sub_path, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')
        data = {
            "state": 1,
            "message": u"成功",
            "url": self.upload_url,
            "language": self.lang,
            "auto": self.auto
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)

    def covert_ttml(self):
        with open(self.insub) as f:
            content = f.read()
            # print content
            selector = Selector(text=content, type="html")
            ps = selector.xpath('//p').extract()
            srt = ""
            for idx, p in enumerate(ps):
                print idx, p
                s = Selector(text=p, type="html")
                timestamp = "%s --> %s" % (s.xpath("//p/@begin").extract()[0], s.xpath("//p/@end").extract()[0])
                text = re.compile(r"<[^>]+>").sub(lambda m: '', p)
                line = idx + 1
                srt += "%s\n%s\n%s\n\n" % (line, timestamp, text)
            if len(srt) > 1:
                self.insub = 'cache/%s_auto.srt' % self.video_id
                print self.insub, srt
                open(self.insub, 'w').write(srt.encode('utf-8'))

    def _match_id(self, url):
        youtube = r'https?://www\.youtube\.com/watch\?v=(?P<id>[0-9A-Za-z_-]{11})'

        m = re.compile(youtube).match(url)
        assert m
        return m.group('id')

    def debug(self, msg):
        print ['MyLogger debug'], msg
        regx = r'\[info\] Writing video subtitles to\: cache/(?P<video_id>[0-9A-Za-z_-]+)\.(?P<lang>[A-Za-z_-]+)\.(?P<ext>\w+)'
        # print regx
        match = re.compile(regx).match(msg)
        if match:
            self.ext = match.group('ext')
            video_id = match.group('video_id')
            self.lang = match.group('lang')
            self.insub = 'cache/%s.%s.%s' % (video_id, match.group('lang'), self.ext)
            print 'insub', self.insub

    def info(self, msg):
        print ['MyLogger info'], msg

    def warning(self, msg):
        print ['MyLogger warning'], msg
        if "video doesn't have subtitles" in msg:
            self.auto = 1

    def error(self, msg):
        print ['MyLogger error'], msg

    def closed(self, reason):
        print '[closed]', reason
        if not self.callbacked:
            data = {
                "state": 0,
                "message": reason,
                "url": ""
            }
            service.utils.callback_result(self.callback, data=data)
            service.mail.send_mail('spider failed:' + self.name, self.uuid + ' ' + reason)
        if self.video_id:
            service.utils.remove_all_files('cache/', self.video_id)
