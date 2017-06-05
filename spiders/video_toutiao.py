# -*- coding: utf-8 -*-
import scrapy
from scrapy.http import Request
import requests
import base64
import ConfigParser
import json
from service.logger import Logger
from service.database import get_database
import service.utils
import time
from scrapy.exceptions import CloseSpider
import re
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('toutiao')


class VideoToutiaoSpider(scrapy.Spider):
    name = "toutiao"
    allowed_domains = ["toutiao.com"]
    callbacked = False
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(VideoToutiaoSpider, self).__init__(*args, **kwargs)

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
        self.headers = {
            'Host': 'www.toutiao.com',
            'Cache-Control': 'max-age=0',
            'Upgrade-Insecure-Requests': 1,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/57.0.2987.133 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'DNT': 1,
            'Accept-Encoding': 'gzip, deflate, sdch',
            'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6,zh-TW;q=0.4',
            'Cookie': 'uuid="w:26f6c318a3d54f359dde999ed9c05acc"; UM_distinctid=15ac5b41a4d64b-0dd4bf7186a738-1d3a6853-1fa400-15ac5b41a4e761; _ba=BA0.2-20170317-51d9e-fPGwZMlLK21rToVv50w2; csrftoken=92f3c0634079d6e3c96068bdcfa65ea3; tt_webid=54911862630; __tasessionId=iscec65nv1494484094230; CNZZDATA1259612802=1805146543-1486616419-%7C1494483501; _ga=GA1.2.1585727393.1486621132; _gid=GA1.2.190446546.1494484408'
        }

    def start_requests(self):
        for url in self.start_urls:
            yield Request(url, callback=self.parse, headers=self.headers)

    def parse(self, response):
        print response.url, len(response.body)

        title = response.selector.xpath('//title/text()').extract()[0].strip()
        print title
        # self.video_id = response.selector.xpath('//@tt-videoid').extract()[0].strip()
        try:
            self.video_id = re.compile(r"videoid\s*:\s*\'(?P<video_id>\w+)\'").search(response.body).group('video_id')
        except Exception, e:
            raise CloseSpider(u"link not supported")

        print 'video_id', self.video_id
        if self.check_db():
            return

        getVideoUrl = 'http://i.snssdk.com/video/urls/1/toutiao/mp4/%s' % self.video_id
        resp = requests.get(getVideoUrl)
        result = resp.json()
        # print result
        if result['code'] == 0:
            data = result['data']
            video_duration = data['video_duration']
            video_list = data['video_list']
            if video_list.has_key('video_3'):
                video = video_list['video_3']
            elif video_list.has_key('video_2'):
                video = video_list['video_2']
            elif video_list.has_key('video_1'):
                video = video_list['video_1']
            video_size = video['size']
            url = base64.decodestring(video['main_url'].strip('\n').strip())
            print 'url-->', url
            print video_size, video_duration

            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            result = service.utils.uploadVideoByUrl(url, endpoint, backet, obj)
            print 'upload result', result
            if not result:
                raise CloseSpider('upload oss failed')

            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": video_duration,
                "play_id": self.uuid,
                "size": video_size,
                "cover": '',
                "title": title
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

            video_data = {
                'title': title,
                'video_id': self.video_id,
                'author': 'toutiao',
                'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
                'page_url': response.url,
                'video_length': video_duration,
                'video_size': video_size,
                'video_url': url,
                'easub_uuid': self.uuid
            }
            self.db.save_video(video_data, table='toutiao')

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
            logger.info('[closed]')
            service.utils.callback_result(self.callback, data=data)
            service.mail.send_mail('spider failed:' + self.name, self.uuid + ' ' + reason)
        logger.info('[closed reason]' + reason + "[uuid]" + self.uuid)
