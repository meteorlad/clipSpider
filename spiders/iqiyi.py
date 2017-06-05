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
import execjs
import requests
import re
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('iqiyi')


class IqiyiSpider(scrapy.Spider):
    name = "iqiyi"
    allowed_domains = ["iqiyi.com", "pps.tv"]
    callbacked = False
    video_id = None

    # start_urls = (
    #     'http://www.www.iqiyi.com/',
    # )

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(IqiyiSpider, self).__init__(*args, **kwargs)
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
        print self.start_urls[0]
        regx = r"https?://www\.iqiyi\.com/(.+)(#|&)curid=(?P<tvid>[0-9]+)_(?P<videoid>[0-9A-Za-z]+)"
        m = re.compile(regx).match(self.start_urls[0])
        print m
        if m:
            print 'url matched'
            self.video_id = m.group('videoid')
            tvid = m.group('tvid')
        else:
            self.video_id = response.selector.xpath('//div/@data-player-videoid').extract()[0].strip()
            tvid = response.selector.xpath('//div/@data-player-tvid').extract()[0].strip()
        print self.video_id
        if not self.video_id:
            raise CloseSpider('link not supported')

        # live
        print 'tvid', tvid, 'video_id', self.video_id
        if self.create_live(tvid):
            return

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        titles = response.xpath('//meta[@name="irTitle"]/@content').extract()
        if len(titles) > 0 and len(titles[0]) > 2:
            self.title = titles[0]
        else:
            self.title = response.xpath('//title/text()').extract()[0]
        print self.title
        self.parse_video(tvid, self.video_id)
        if self.callbacked:
            return

        self.parsePlayUrl(response.url)

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

    def create_live(self, tvid):
        live_url = self.get_live_url(tvid)
        logger.info(live_url)
        resp = requests.get(live_url)
        content = service.utils.remove_start(resp.content, 'var tvInfoJs=')
        cover = ''
        live_play_url = None
        title = ''
        if len(content) > 2:
            info = json.loads(content)
            if info['code'] == 'A00000':
                title = info['data']['name']

                streams = info['data']['streams']
                for stream in streams:
                    if stream['formatType'] == 'TS':
                        live_play_url = stream['url']

        if live_play_url:
            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'直播',
                "cover": cover,
                "title": title,
                "live_url": live_play_url
            }
            print 'create live:', live_play_url
            self.callbacked = service.utils.callback_result(self.live_callback, data)
            return True

    def parse_video(self, tvid, video_id):
        try:
            info_url = self.get_video_info_url(tvid, video_id)
            print info_url
            resp = requests.get(info_url, )
            content = resp.content[len('try{tmtsCallback('):-len(');}catch(e){};') - 1]
            print content
            if len(content) > 2:
                info = json.loads(content)
                if 'playInfo' in content:
                    title = info['data']['playInfo']['shortTitle']
                    if len(title) < 2:
                        title = info['data']['playInfo']['vn']
                else:
                    title = self.title
                video_url = info['data']['m3u']
                length = info['data']['duration']
                video_path = 'cache/%s.mp4' % self.uuid
                print video_url
                r = requests.head(video_url)
                if 'text' in r.headers['Content-Type']:
                    c = requests.get(video_url).content
                    m = re.compile(r'"l":"(?P<url>[^"]+)').search(c)
                    if not m:
                        return
                    video_url = m.group('url')
                total_size, success = service.utils.download_file(video_url, video_path)
                if not success:
                    print 'download video error', video_url
                    return
                if length < 1:
                    length = service.utils.getVideoLength(video_path)
                endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
                print endpoint, backet, obj
                result = service.utils.uploadVideo(video_path, endpoint, backet, obj)
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
                    "size": total_size,
                    "cover": cover,
                    "title": title
                }
                self.callbacked = service.utils.callback_result(self.callback, data=data)
                logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

                video_data = {
                    'title': title,
                    'video_id': video_id,
                    'author': 'iqiyi',
                    'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'page_url': self.start_urls[0],
                    'video_length': length,
                    'video_size': total_size,
                    'video_url': '',
                    'easub_uuid': self.uuid,
                    'cover': cover
                }
                self.db.save_video(video_data)
        except Exception, e:
            print 'exception', e
            return


    def parsePlayUrl(self, playUrl):
        print playUrl

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': '17/21/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            # 'noplaylist': True
        }
        print ydl_opts
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([playUrl])

    def hooks(self, d):

        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())
            os.remove(jsonfile)

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

            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": length,
                "play_id": self.uuid,
                "size": total_bytes,
                "cover": '',
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
                'easub_uuid': self.uuid
            }
            self.db.save_video(video_data)
            raise CloseSpider('finished')

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def get_live_url(self, tvid):
        live_url_base = 'http://live.video.iqiyi.com'
        query = '/jp/live?lc=&lp=%s&src=02020031010000000000&rateVers=H5_QIYI&t=%s&uid=&v=0&qdv=1&qdx=n&qds=0&__jsT=sgve' % (
            tvid, str(int(time.time() * 1000)))
        ctx = execjs.compile(open('service/cmd5x.js').read())
        vf = ctx.call('cmd5x', query)
        query += '&vf=' + vf
        return live_url_base + query

    def get_video_info_url(self, tvid, video_id):
        video_url_base = 'http://cache.m.iqiyi.com'
        qyid = 'j2pv8zzc8umpbi9q8zc85af3'
        #        /jp/tmts/550832300/d728a82406541657326c04f9ee070035/?uid=&cupid=qc_100001_100186&platForm=h5&qyid=j2pv8zzc8umpbi9q8zc85af3&agenttype=13&type=mp4&rate=1&qdv=1&qdx=n&qds=0&__jsT=sgve&t=1476412447241&src=02020031010000000000&callback=tmtsCallback&vf=297386bd0661fbebe41a84875dba81c4
        query = '/jp/tmts/%s/%s/?uid=&cupid=qc_100001_100186&platForm=h5&qyid=%s&agenttype=13&type=mp4&rate=1&k_ft1=8&qdv=1&qdx=n&qdy=x&qds=0&__jsT=sgve&t=%s&src=02020031010000000000&callback=tmtsCallback' % (
            tvid, video_id, qyid, str(int(time.time() * 1000)))
        print 'query', query
        ctx = execjs.compile(open('service/cmd5x.js').read())
        vf = ctx.call('cmd5x', query)
        query += '&vf=' + vf
        return video_url_base + query

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
