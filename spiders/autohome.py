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
import time
import youtube_dl
import os
import string
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('autohome')


class AutohomeSpider(scrapy.Spider):
    name = "autohome"
    allowed_domains = ["autohome.com"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, live_callback=None, *args, **kwargs):
        super(AutohomeSpider, self).__init__(*args, **kwargs)

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
        print self.start_urls[0]

        try:
            src = response.xpath('//iframe/@src').extract()
            if len(src) > 0 and len(src[0]):
                iframe_url = src[0]
                print iframe_url
                self.video_id = re.compile(r"vid=(?P<vid>\d+)").search(iframe_url).group('vid')
                self.start_urls[0] = "http://sports.le.com/video/%s.html" % self.video_id
                print self.start_urls[0]
            else:
                self.video_id = self.match_id(response.body)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        if self.check_db():
            return

        if 'autohome' in self.start_urls[0]:
            self.get_autohome_video(response)
            return

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': 'http-720p/high/hd/22/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            # 'noplaylist': True,
            'max_downloads': 1
        }

        print ydl_opts

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download(self.start_urls)

    def get_autohome_video(self, response):
        get_info_url = "http://p-vp.autohome.com.cn/api/gmi?mid=%s&useragent=Android&callback=a" % self.video_id
        resp = requests.get(get_info_url)
        content = resp.content[2:-1]
        print content
        info = json.loads(content)
        title = response.xpath("//title/text()").extract()[0]

        file_path = "cache/%s.mp4" % self.uuid
        file_size, success = service.utils.download_file(info['copies'][1]['playurl'], file_path)
        if not success:
            raise CloseSpider('download video failed')
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            logger.error('upload video error')
            raise CloseSpider('upload oss failed')

        cover = service.utils.get_clip_cover_url(info['img'], self.uuid)
        length = info['duration']
        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
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
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
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

    def hooks(self, d):

        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())
            os.remove(jsonfile)
            if 'duration' not in info:
                info['duration'] = service.utils.getVideoLength(filename)
            total_bytes = os.path.getsize(filename)
            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            uploadResult = service.utils.uploadVideo(filename, endpoint, backet, obj)

            if not uploadResult:
                raise CloseSpider('upload oss failed')
            # os.remove('cache/' + info['id'] + '*')
            if 'thumbnail' in info:
                cover = service.utils.get_clip_cover_url(info['thumbnail'], self.uuid)
            else:
                cover = ''

            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": info['duration'],
                "play_id": self.uuid,
                "size": total_bytes,
                "cover": cover,
                "title": info['title']
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

            if 'uploader' not in info:
                info['uploader'] = info['extractor']

            video_data = {
                'title': info['title'],
                'video_id': self.video_id,
                'author': info['uploader'],
                'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
                'page_url': info['webpage_url'],
                'video_length': info['duration'],
                'video_size': total_bytes,
                'video_url': '',
                'easub_uuid': self.uuid,
                'cover': cover
            }
            self.db.save_video(video_data)
            raise CloseSpider('finished')

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def match_id(self, url):
        regx = r"vid:\s*\"(?P<vid>[a-z0-9A-Z]+)\""
        regx2 = r"vid=(?P<vid>[a-z0-9A-Z]+)"
        m = re.compile(regx).search(url) or re.compile(regx2).search(url)
        assert m
        return m.group('vid')

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
