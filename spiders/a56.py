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
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('56')


class A56Spider(scrapy.Spider):
    name = "56"
    allowed_domains = ["56.com"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(A56Spider, self).__init__(*args, **kwargs)

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

        try:
            if self.you_get():
                return
        except ValueError, e:
            print e

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

    def you_get(self):
        command = ['you-get', '--json', self.start_urls[0]]
        print command
        stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print 'stdout', stdout, 'stderr', stderr
        if len(stdout) < 2:
            return False

        logger.info('[you-get]' + '[uuid]' + self.uuid)
        video = json.loads(stdout)
        if 'streams' not in video:
            return False
        title = video['title']
        srcs = []
        for key in video['streams'].keys():
            print key
            if 'src' in video['streams'][key]:
                srcs = video['streams'][key]['src']
                print srcs
                break
        concatfile = 'cache/' + self.uuid + '.txt'
        mp4file = 'cache/' + self.uuid + '.mp4'
        for idx, src in enumerate(srcs):
            src_path = 'cache/' + self.uuid + '_' + str(idx) + '.mp4'
            _, success = service.utils.download_file(src, src_path)
            if not success:
                return False
            open(concatfile, 'a+').write('file ' + string.replace(src_path, 'cache/', '') + "\n")
        length = service.utils.mergeVideo(mp4file, concatfile)
        print '[merged video duration]', length
        if length == 0:
            return False
        filesize = os.path.getsize(mp4file)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        print 'uploadResult:', uploadResult
        if not uploadResult:
            return False

        logger.warn('[uploadVideo]' + '[uuid]' + self.uuid)

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
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': '',
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)
        raise CloseSpider('finished')

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

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def match_id(self, url):
        c56 = r'https?://(?:(?:www|player)\.)?56\.com/(?:.+?/)?(?:v_|(?:play_album.+-))(?P<id>.+?)\.(?:html|swf)'
        m = re.compile(c56).match(url)
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
