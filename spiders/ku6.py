# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
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
from scrapy.exceptions import CloseSpider
import requests
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('dl.youtube')


class Ku6Spider(scrapy.Spider):
    name = "ku6"
    allowed_domains = ["ku6.com"]
    callbacked = False
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(Ku6Spider, self).__init__(*args, **kwargs)

        self.start_urls.append(url)
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

    def parse(self, response):
        print response.url

        try:
            self.video_id = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        self.get_video()
        if self.callbacked:
            return

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': 'http-720p/high/hd/22/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            'max_downloads': 1
        }

        print ydl_opts

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download(self.start_urls)

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

    def get_video(self):
        # http://v.ku6.com/fetch.htm?t=getVideo4Player&vid=Tn-DuzxZSJVAjABcByJ7tQ..&stype=mp4
        # vid = re.split(r"show/", self.start_urls[0])[1][:-5]
        # print 'vid', vid
        data_url = 'http://v.ku6.com/fetch.htm?t=getVideo4Player&vid=%s&stype=mp4' % self.video_id
        info = requests.get(data_url).json()
        print data_url, info
        if 'status' not in info or info['status'] != 1:
            return

        video_url = info['data']['f']
        title = info['data']['t']
        file_path = 'cache/' + self.uuid
        filesize, success = service.utils.download_file(video_url, file_path)
        if not success:
            return

        video_path = 'cache/%s.mp4' % self.uuid
        length = service.utils.coverterMp4(file_path, video_path)

        if not length:
            raise CloseSpider('covert failed')
        total_bytes = os.path.getsize(video_path)

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(video_path, endpoint, backet, obj)
        # os.remove('cache/' + info['id'] + '*')
        if not result:
            raise CloseSpider('upload oss failed')

        print 'easub_uuid', result

        cover = service.utils.get_clip_cover_url(info['data']['bigpicpath'], self.uuid)
        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": total_bytes,
            "cover": cover,
            "title": title
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': title,
            'video_id': self.video_id,
            'author': "Ku6",
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': total_bytes,
            'video_url': '',
            'easub_uuid': self.uuid,
            'cover': cover
        }
        self.db.save_video(video_data)

    def hooks(self, d):
        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())

            outpath = 'cache/' + self.uuid + '_.mp4'
            length = service.utils.coverterMp4(filename, outpath)
            print length, outpath
            if not length:
                raise CloseSpider('covert failed')
            total_bytes = os.path.getsize(outpath)

            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            result = service.utils.uploadVideo(outpath, endpoint, backet, obj)
            # os.remove('cache/' + info['id'] + '*')
            if not result:
                raise CloseSpider('upload oss failed')

            print 'easub_uuid', result

            if 'thumbnail' in info:
                cover = service.utils.get_clip_cover_url(info['thumbnail'], self.uuid)
            else:
                cover = ''
            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": length,
                "play_id": self.uuid,
                "size": total_bytes,
                "cover": cover,
                "title": info['title']
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

            video_data = {
                'title': info['title'],
                'video_id': self.video_id,
                'author': info['extractor'],
                'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
                'page_url': info['webpage_url'],
                'video_length': length,
                'video_size': total_bytes,
                'video_url': '',
                'easub_uuid': self.uuid,
                'cover': cover
            }
            self.db.save_video(video_data)

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def match_id(self, url):
        # http://v.ku6.com/film/show_136800/eYs9i_NrJAigLtM-clrDmQ...html
        ku6 = r'https?://v\.ku6\.com(/film)?/show(_\d+)?/(?P<id>[a-zA-Z0-9\-\_\.]+)\.html'
        m = re.compile(ku6).match(url)
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
