# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import youtube_dl
import string
import json
import subprocess
import os
import time
from service.database import get_database
from service.logger import Logger
import service.utils
from scrapy.exceptions import CloseSpider
import requests
import re
import urllib
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('sina')


class SinaSpider(scrapy.Spider):
    name = "sina"
    allowed_domains = ["sina.com"]
    callbacked = False
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(SinaSpider, self).__init__(*args, **kwargs)
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
            self.video_id = self.match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        print 'video_id', self.video_id
        # live
        if self.live():
            return

        # db
        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        self.parse_play_url(self.start_urls[0])

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

    def live(self):
        live_url = 'http://s.video.sina.com.cn/live/play?program_id=%s' % self.video_id
        print live_url
        resp = requests.get(live_url)
        info = resp.json()
        print info
        if info['code'] == 1:
            live_play_url = None
            # end_time = None
            title = info['data']['title']
            cover = info['data']['image']
            for stream in info['data']['streams']:
                if stream['protocal'] == 'rtmp':
                    address = stream['address']
                    print 'live address', address
                    result = requests.get(address)
                    content = result.content
                    # end_time = stream['end_time']
                    lines = re.split("\n", content)
                    for line in lines:
                        if '_hd.m3u8' in line:
                            live_play_url = line
                            print 'live play url:', live_play_url

            if live_play_url:
                data = {
                    "video_id": self.uuid,
                    "state": 1,
                    "message": u'直播',
                    "cover": cover,
                    "title": title,
                    "live_url": live_play_url,
                    # "end_time": end_time
                }
                self.callbacked = service.utils.callback_result(self.live_callback, data)
                return True

    def parse_play_url(self, play_url):
        print play_url

        self.get_video()
        if self.callbacked:
            return

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': '17/21/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            # 'noplaylist': True,
            'progress_hooks': [self.hooks]
        }
        print ydl_opts
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([play_url])

    def get_video(self):
        h5play = 'http://s.video.sina.com.cn/video/h5play?video_id=' + self.video_id
        info = requests.get(h5play).json()
        if info['code'] != 1:
            return

        title = info['data']['title']
        cover = info['data']['image']
        if 'mp4' not in info['data']['videos']:
            return
        mp4 = info['data']['videos']['mp4']
        if 'hd' in mp4:
            mp4 = mp4['hd']
        else:
            mp4 = mp4['sd']
        video_url = mp4['file_api'] + "?vid=" + mp4['file_id']
        length = int(mp4['length']) / 1000
        # total_bytes = mp4['size']
        file_path = 'cache/%s.mp4' % self.uuid
        total_bytes, success = service.utils.download_file(video_url, file_path)
        if not success:
            raise CloseSpider('download failed')

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            logger.error('upload video error', self.uuid)
            raise CloseSpider('upload oss failed')

        print 'easub_uuid', result

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
            'author': 'sina',
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': total_bytes,
            'video_url': video_url,
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

            total_bytes = os.path.getsize(filename)
            length = info['duration'] / 1000
            print 'total_bytes', total_bytes, 'length', length
            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            result = service.utils.uploadVideo(filename, endpoint, backet, obj)
            if not result:
                logger.error('upload video error', self.uuid)
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
            raise CloseSpider('finished')

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def get_live_url(self, tvid):
        pass

    def match_id(self, url):
        regx = r'''(?x)https?://(?:.*?\.)?(video|ent)\.sina\.com\.cn/
                        (?:
                            (?:view/|.*\#)(?P<video_id>\d+)|
                            .+?/(?P<pseudo_id>[^/?#]+)(?:\.s?html)|
                            # This is used by external sites like Weibo
                            api/sinawebApi/outplay.php/(?P<token>.+?)\.swf
                        )
                  '''
        m = re.compile(regx).match(url)
        assert m
        return m.group('video_id') or m.group('pseudo_id')

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
