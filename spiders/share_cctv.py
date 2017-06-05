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
import youtube_dl
import string
import os
import subprocess
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('share.cctv')


class ShareCctvSpider(scrapy.Spider):
    name = "share.cctv"
    allowed_domains = ["cctv.com"]
    callbacked = False
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(ShareCctvSpider, self).__init__(*args, **kwargs)

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
            # r'(?:fo\.addVariable\("videoCenterId",\s*|guid\s*=\s*)"([0-9a-f]{32})'
            m = re.compile(r'(?:fo\.addVariable\("videoCenterId",\s*|guid\s*=\s*)"(?P<video_id>[0-9a-f]{32})').search(
                response.body)
            if m:
                self.video_id = m.group('video_id')
            else:
                self.video_id = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        try:
            if self.you_get():
                return
        except ValueError, e:
            print e

        self.share_cctv()
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
                "cover": '',
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

            outpath = 'cache/' + self.uuid + '_.mp4'
            length = service.utils.coverterMp4(filename, outpath)
            print length, outpath
            if not length:
                logger.error('error trancode mp4' + self.uuid)
                raise CloseSpider('covert failed')
            total_bytes = os.path.getsize(outpath)

            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            result = service.utils.uploadVideo(outpath, endpoint, backet, obj)
            # os.remove('cache/' + info['id'] + '*')
            if not result:
                self.logger.error('upload video error', self.uuid)
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

    def share_cctv(self):
        video_info = 'http://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid=%s' % self.video_id
        print video_info
        resp = requests.get(video_info)
        info = resp.json()
        print info
        if 'video' in info:
            title = info['title']
            video = info['video'][info['default_stream']][0]
            print video
            video_url = video['url']
            length = video['duration']
            self.upload_video(video_url, title, length)
            return

        getinfo_url = 'http://api.saas.tvm.cn/share/getbyid.php?topicid=%s' % self.video_id
        print getinfo_url
        resp = requests.get(getinfo_url)
        info = resp.json()
        print info

        if 'entry' in info:
            video = info['entry'][0]
            content = video['media']['content']
            video_url = content['url']
            title = video['title']
            self.upload_video(video_url, title, 0)
            return

        getinfo_url = 'http://api.5club.cctv.cn/mobileinf/rest/cardgroups/share?json={"cardgroups":"%s"}' % self.video_id
        print getinfo_url
        resp = requests.get(getinfo_url)
        info = resp.json()
        print info
        if 'cardgroups' not in info:
            return
        for cardgroup in info['cardgroups']:
            cards = cardgroup['cards']
            for card in cards:
                if card['id'] == self.video_id:
                    title = card['title']
                    video_url = card['video']['url']
                    length = card['video']['duration']
                    self.upload_video(video_url, title, length)
                    return

    def upload_video(self, video_url, title, length):
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        file_path = 'cache/%s.mp4' % self.uuid
        filesize, success = service.utils.download_file(video_url, file_path)
        if not success:
            logger.error('download video error', video_url)
            raise CloseSpider('download video failed')
        if length < 1:
            length = service.utils.getVideoLength(file_path)

        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            logger.error('upload video error', video_url)
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
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': video_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)

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
        share_cctv = r'https?://(?:www\.)?(?:v1\.tv\.cctv.*\.)?cctv\.com/ishare/play/index.html\?(.*&)?id=(?P<id>[\d]+)'

        cctv = r'''(?x)https?://(?:.+?\.)?
        (?:
            cctv\.(?:com|cn)|
            cntv\.cn
        )/
        (?:
            video/[^/]+/(?P<id>[0-9a-f]{32})|
            \d{4}/\d{2}/\d{2}/(?P<display_id>VID[0-9A-Za-z]+)
        )'''
        app_cctv = r'https?://api\.5club\.cctv\.cn/cctvsports/cctv5/cctvsportsshare/video/video\.html\?(.*&)?id=(?P<id>[0-9A-Za-z]+)'
        m = re.compile(cctv).match(url) or re.compile(share_cctv).match(url) or re.compile(app_cctv).match(url)
        assert m
        return m.group('id') or m.group('display_id')
