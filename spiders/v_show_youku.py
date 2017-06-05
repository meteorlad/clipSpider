# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import requests
import youtube_dl
import string
import json
import subprocess
import os
import time
import re
from service.database import get_database
import service.utils
from service.logger import Logger
from scrapy.exceptions import CloseSpider
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('youku')


class VShowYoukuSpider(scrapy.Spider):
    name = "youku"
    allowed_domains = ["youku.com"]
    # start_urls = (
    #     'http://www.i.youku.com/',
    # )
    logger = None
    callbacked = False

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(VShowYoukuSpider, self).__init__(*args, **kwargs)
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
            self.video_id = self._match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        print self.get_video(response)

        if self.callbacked:
            return

        try:
            if self.you_get(response.xpath('//title/text()').extract()[0]):
                return
        except:
            print 'you-get failed'

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': '22/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            # 'noplaylist': True,
            # 'logger': MyLogger(),
        }
        print ydl_opts

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download(self.start_urls)

    def get_video(self, response):
        print 'get video'
        # info_url = 'https://ups.youku.com/ups/get.json?vid=%s&ccode=0402&client_ip=192.168.1.1&utid=w%2F5rDrwhFnECAYuiBtB1QPO8&client_ts=1493192983&password=null'
        # utid = requests.get('https://log.mmstat.com/eg.js').content
        # utid = re.compile('".*"').search(utid).group(0)[1:-1]
        utid = 'w/5rDrwhFnECAYuiBtB1QPO8'
        clent_ts = int(time.time())  # 普通时间戳
        ccode = '0401'
        info_url = 'https://ups.youku.com/ups/get.json?vid=%s&ccode=%s&client_ip=192.168.1.1&utid=%s&client_ts=%s&password=null' % (
        self.video_id, ccode, utid, clent_ts)

        print info_url
        resp = requests.get(info_url)
        print resp.content
        info = resp.json()
        if ('data' not in info) or ('stream' not in info['data']):
            return
        title = info['data']['video']['title']
        path = 'cache/%s.mp4' % self.uuid
        concatfile = 'cache/' + self.uuid + '.txt'
        for i in info['data']['stream']:
            if 'mp4hd' in i['stream_type'] or 'flvhd' in i['stream_type']:
                # video_url = i['segs'][0]['cdn_url']
                # video_size = i['size']
                for idx, seg in enumerate(i['segs']):
                    video_url = seg['cdn_url']
                    tmp_path = 'cache/%s_%s.tmp' % (self.uuid, idx)
                    print tmp_path, video_url
                    filesize, success = service.utils.download_file(video_url, tmp_path)
                    if not success:
                        return False
                    open(concatfile, 'a+').write('file ' + string.replace(tmp_path, 'cache/', '') + "\n")
                break

        length = service.utils.mergeVideo(path, concatfile)
        filesize = os.path.getsize(path)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(path, endpoint, backet, obj)
        if not uploadResult:
            return False

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
        raise CloseSpider('get video success')


    def you_get(self, title):
        print title
        dir_path = 'cache/%s/' % self.uuid
        command = ['you-get', '-n', '-o', dir_path, self.start_urls[0]]
        print command
        stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print 'stdout', stdout, 'stderr', stderr
        if stderr:
            return False
        logger.info('[you-get]' + '[uuid]' + self.uuid)

        concatfile = 'cache/' + self.uuid + '.txt'
        dirs = os.listdir(dir_path)
        for idx, file in enumerate(sorted(dirs)):
            new = dir_path + str(idx) + '.mp4'
            print dir_path + file, new
            os.rename(dir_path + file, new)
            open(concatfile, 'a+').write('file ' + string.replace(new, 'cache/', '') + "\n")
        mp4file = 'cache/' + self.uuid + '.mp4'
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
        raise CloseSpider('you-get success')

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
            # id = info['playlist_id']
            concatfile = 'cache/' + self.uuid + '.txt'
            open(concatfile, 'a+').write('file ' + string.replace(filename, 'cache/', '') + "\n")
            if ('n_entries' not in info) or (info['playlist_index'] == info['n_entries']):
                mp4file = 'cache/' + self.uuid + '.mp4'
                length = service.utils.mergeVideo(mp4file, concatfile)
                print '[merged video duration]', length
                if length == 0:
                    raise CloseSpider('merge video failed')
                filesize = os.path.getsize(mp4file)
                endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
                print endpoint, backet, obj
                uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
                print 'uploadResult:', uploadResult
                if not uploadResult:
                    raise CloseSpider('upload oss failed')

                logger.warn('[uploadVideo]' + '[uuid]' + self.uuid)
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

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def _match_id(self, url):
        # tudou = r'https?://(?:www\.)?tudou\.com/(?:(?:programs|wlplay)/view|(?:listplay|albumplay)/[\w-]{11})/(?P<id>[\w-]{11})'
        # tudou_album = r'https?://(?:www\.)?tudou\.com/album(?:cover|play)/(?P<id>[\w-]{11})'
        youku = r'''(?x)
                    (?:
                        http://(?:v|player)\.youku\.com/(?:v_show/id_|player\.php/sid/)|
                        youku:)
                    (?P<id>[A-Za-z0-9]+)(?:\.html|/v\.swf|)
                '''
        # m = re.compile(tudou).match(url) or re.compile(youku).match(url) or re.compile(tudou_album).match(url)
        m = re.compile(youku).match(url)
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
