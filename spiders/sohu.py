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
from scrapy.exceptions import CloseSpider
import requests
import urllib
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('sohu')


class SohuSpider(scrapy.Spider):
    name = "sohu"
    allowed_domains = ["sohu.com"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(SohuSpider, self).__init__(*args, **kwargs)

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
            self.video_id, mytv = self.match_id(response.url)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        self.get_video(mytv, response.body)
        if self.callbacked:
            return

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': 'http-720p/high/hd/22/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            # 'noplaylist': True
            # 'max_downloads': 1
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

    def get_video(self, mytv, body):
        try:
            if mytv:
                base_data_url = 'http://my.tv.sohu.com/play/videonew.do?vid='
            else:
                base_data_url = 'http://hot.vrs.sohu.com/vrs_flash.action?vid='

            vid = re.search(r'var vid ?= ?["\'](?P<vid>\d+)["\']', body).group('vid')
            info_url = base_data_url + vid
            print info_url
            info = requests.get(info_url).json()
            print info
            title = info['data']['tvName']
            allot = info['allot']
            clips_url = info['data']['clipsURL']
            su = info['data']['su']
            concatfile = 'cache/' + self.uuid + '.txt'
            for i, clip_url in enumerate(clips_url):
                params = {
                    'prot': 9,
                    'file': clip_url,
                    'new': su[i],
                    'prod': 'flash',
                    'rb': 1,
                }
                video_part_url = 'http://%s/?%s' % (allot, urllib.urlencode(params))
                print video_part_url
                url = requests.get(video_part_url).json()['url']
                print url
                filename = 'cache/%s_%s' % (self.uuid, i)
                filesize, success = service.utils.download_file(url, filename)
                if not success:
                    raise CloseSpider('download failed')

                open(concatfile, 'a+').write('file ' + string.replace(filename, 'cache/', '') + "\n")

            mp4file = 'cache/' + self.uuid + '.mp4'
            length = service.utils.mergeVideo(mp4file, concatfile)
            print '[merged video duration]', length
            if length == 0:
                raise CloseSpider('merge video failed')

            self.upload({"fulltitle": title, "extractor": "Sohu", "thumbnail": info['data']['coverImg']}, mp4file)

        except Exception, e:
            print e

    def hooks(self, d):

        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())
            # id = info['playlist_id']
            if 'n_entries' not in info:
                self.upload(info, filename)
                return

            concatfile = 'cache/' + self.uuid + '.txt'
            open(concatfile, 'a+').write('file ' + string.replace(filename, 'cache/', '') + "\n")

            if info['playlist_index'] == info['n_entries']:
                mp4file = 'cache/' + self.uuid + '.mp4'
                length = service.utils.mergeVideo(mp4file, concatfile)
                print '[merged video duration]', length
                if length == 0:
                    raise CloseSpider('merge video failed')
                self.upload(info, mp4file)

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def upload(self, info, mp4file):
        filesize = os.path.getsize(mp4file)
        length = service.utils.getVideoLength(mp4file)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        print 'uploadResult:', uploadResult
        if not uploadResult:
            raise CloseSpider('upload oss failed')

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
            "size": filesize,
            "cover": cover,
            "title": info['fulltitle']
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': info['fulltitle'],
            'video_id': self.video_id,
            'author': info['extractor'],
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': '',
            'easub_uuid': self.uuid,
            'cover': cover
        }
        self.db.save_video(video_data)
        raise CloseSpider('finished')

    def match_id(self, url):
        sohu = r'https?://(?P<mytv>my\.)?tv\.sohu\.com/.+?/(?(mytv)|n)(?P<id>\d+)\.shtml.*?'
        m = re.compile(sohu).match(url)
        assert m
        return m.group('id'), m.group('mytv') is not None

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
