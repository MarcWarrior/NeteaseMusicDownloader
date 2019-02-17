#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# 项目基于https://github.com/Jack-Cherish/python-spider/blob/master/Netease/Netease.py
import os
import re
import sys
import json
import time
import codecs
import base64
import asyncio
import aiohttp
import chardet
import aiofiles
import binascii
from http import cookiejar
from printer import Printer
from Crypto.Cipher import AES
from contextlib import closing


MAX_SEMAPHORE = asyncio.Semaphore(10)


class Encrypyed:
    """
    解密算法
    """
    def __init__(self):
        self.modulus = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
        self.nonce = '0CoJUm6Qyw8W8jud'
        self.pub_key = '010001'

    # 登录加密算法, 基于https://github.com/stkevintan/nw_musicbox脚本实现
    def encrypted_request(self, text):
        text = json.dumps(text)
        sec_key = self.create_secret_key(16)
        enc_text = self.aes_encrypt(self.aes_encrypt(text, self.nonce), sec_key.decode('utf-8'))
        enc_sec_key = self.rsa_encrpt(sec_key, self.pub_key, self.modulus)
        data = {'params': enc_text, 'encSecKey': enc_sec_key}
        return data

    @staticmethod
    def aes_encrypt(text, sec_key):
        pad = 16 - len(text) % 16
        text = text + chr(pad) * pad
        encryptor = AES.new(sec_key.encode('utf-8'), AES.MODE_CBC, b'0102030405060708')
        ciphertext = encryptor.encrypt(text.encode('utf-8'))
        ciphertext = base64.b64encode(ciphertext).decode('utf-8')
        return ciphertext

    @staticmethod
    def rsa_encrpt(text, pub_key, modulus):
        text = text[::-1]
        rs = pow(int(binascii.hexlify(text), 16), int(pub_key, 16), int(modulus, 16))
        return format(rs, 'x').zfill(256)

    @staticmethod
    def create_secret_key(size):
        return binascii.hexlify(os.urandom(size))[:16]


class Song:
    """
    歌曲对象，用于存储歌曲的信息
    """
    def __init__(self, song_id, song_name, song_author, song_url=None):
        self.song_id = song_id
        self.song_name = song_name
        self.song_author = song_author
        self.song_url = '' if song_url is None else song_url


class Crawler:
    """
    网易云爬取API
    """
    def __init__(self, timeout=60, cookie_path='.'):
        self.headers = {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip,deflate,sdch',
            'Accept-Language': 'zh-CN,zh;q=0.8,gl;q=0.6,zh-TW;q=0.4',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Host': 'music.163.com',
            'Referer': 'http://music.163.com/search/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) \
            Chrome/63.0.3239.132 Safari/537.36'
        }
        self.timeout = timeout
        self.cookie_path = cookie_path
        self.ep = Encrypyed()

    async def post_request(self, url, params):
        """
        Post请求
        :return: 字典
        """
        data = self.ep.encrypted_request(params)
        async with aiohttp.ClientSession(cookies=cookiejar.LWPCookieJar(self.cookie_path)) as session:
            async with session.post(url, data=data, headers=self.headers, timeout=self.timeout) as resp:
                if resp.status != 200:
                    Printer().error(f'POST请求失败，{resp.reason}')
                else:
                    return await resp.json(content_type=resp.content_type)

    async def search(self, search_content, search_type, limit=9):
        """
        搜索API
        :params search_content: 搜索内容
        :params search_type: 搜索类型
        :params limit: 返回结果数量
        :return: 字典.
        """
        url = 'http://music.163.com/weapi/cloudsearch/get/web?csrf_token='
        params = {'s': search_content, 'type': search_type, 'offset': 0, 'sub': 'false', 'limit': limit}
        result = await self.post_request(url, params)
        return result

    async def search_song(self, song_name, quiet=True, limit=9):
        """
        根据音乐名搜索
        :params song_name: 音乐名
        :params quiet: 自动选择匹配最优结果
        :params limit: 返回结果数量
        :return: Song独享
        """
        result = await self.search(song_name, search_type=1, limit=limit)

        if result['result']['songCount'] <= 0:
            Printer().error(f'歌曲 [{song_name}] 不存在.')
        else:
            songs = result['result']['songs']
            if quiet:
                song_id, song_name = songs[0]['id'], songs[0]['name']
                author_name = songs[0]['ar'][0]['name']
                return Song(song_id=song_id, song_name=song_name, song_author=author_name)

    async def get_song_url(self, song_id, bit_rate=320000):
        """
        获得歌曲的下载地址
        :params song_id: 音乐ID<int>.
        :params bit_rate: {'MD 128k': 128000, 'HD 320k': 320000}
        :return: 歌曲下载地址
        """
        url = 'http://music.163.com/weapi/song/enhance/player/url?csrf_token='
        csrf = ''
        params = {'ids': [song_id], 'br': bit_rate, 'csrf_token': csrf}
        result = await self.post_request(url, params)

        # 歌曲下载地址
        song_url = result['data'][0]['url']

        # 歌曲不存在
        if song_url is None:
            Printer().error('歌曲因版权问题无法下载')
        else:
            return song_url

    async def get_song_by_url(self, song_url, song: Song, folder):
        """
        下载歌曲到本地
        :params song_url: 歌曲下载地址
        :params song_name: 歌曲名字
        :params song_num: 下载的歌曲数
        :params folder: 保存路径
        """
        if not os.path.exists(folder):
            os.makedirs(folder)

        if sys.platform == 'win32' or sys.platform == 'cygwin':
            song_name = re.sub(r'[<>:"/\\|?*]', '', song.song_name)
            file_name = song.song_author + ' - ' + song_name + '.mp3'
        else:
            file_name = song.song_author + ' - ' + song.song_name + '.mp3'

        file_path = os.path.join(folder, file_name)

        if not os.path.exists(file_path):
            size = 0
            async with MAX_SEMAPHORE:
                async with aiohttp.ClientSession() as session:
                    async with session.get(song_url, timeout=self.timeout, chunked=True) as resp:
                        if resp.status == 200:
                            length = int(resp.headers['content-length'])

                            Printer().info(f'正在下载 {song.song_name}.mp3 [{round(length / 1024 / 1024, 2)}MB]...')

                            async with aiofiles.open(file_path, 'wb') as song_file:
                                async for chunk in resp.content.iter_chunked(1024):
                                    if chunk:
                                        await song_file.write(chunk)
                                        size += len(chunk)
                                        await song_file.flush()

                            if size / length == 1:
                                Printer().info(f'歌曲 [{file_name}] 下载完成，保存>>> {file_path}')
                        else:
                            Printer().error(f'歌曲 [{file_name}] 下载失败，下载请求错误')
        else:
            Printer().warning(f'歌曲 [{file_name}] 已存在')


class Netease:
    """
    网易云音乐下载
    """
    def __init__(self, timeout=60, folder='Musics', quiet=True, cookie_path='Cookie'):
        self.crawler = Crawler(timeout, cookie_path)
        self.folder = folder
        self.quiet = quiet

    async def download_song_by_search(self, song_name):
        """
        根据歌曲名进行搜索
        :params song_name: 歌曲名字
        """
        song = None
        try:
            song = await self.crawler.search_song(song_name, self.quiet)
        except Exception as e:
            Printer().error(e)

        # 如果找到了音乐, 则下载
        if song is not None:
            await self._download_song_by_id(song, self.folder)

    async def _download_song_by_id(self, song: Song, folder):
        """
        通过歌曲的ID下载
        :params song: 歌曲对象
        :params folder: 保存地址
        """
        try:
            url = await self.crawler.get_song_url(song.song_id)
            # 去掉非法字符
            song_name = song.song_name.replace('/', '')
            song.song_name = song_name.replace('.', '')
            await self.crawler.get_song_by_url(url, song, folder)
        except Exception as e:
            Printer().error(e)


def exec_time(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        Printer().info(f'共耗时 {round(time.time() - start_time, 2)} 秒')
        return result

    return wrapper


@exec_time
def run():
    api = Netease()

    with open('music_list.txt', 'rb') as f1:
        charset = chardet.detect(f1.read())

    _music_list = []
    with codecs.open('music_list.txt', 'r', encoding=charset['encoding']) as f1:
        for line in f1.readlines():
            _music_list.append(line)

    # 去重排序
    # music_list = list(set(_music_list))  # 随机顺序排序
    music_list = {}.fromkeys(_music_list).keys()  # 字典顺序排序

    Printer().info('歌曲下载列表加载完成...')

    tasks = []
    for song_name in music_list:
        tasks.append(asyncio.ensure_future(api.download_song_by_search(song_name)))

    with closing(asyncio.get_event_loop()) as loop:
        loop.run_until_complete(asyncio.wait(tasks))


if __name__ == '__main__':
    if sys.version_info[0] == 3 and sys.version_info[1] >= 7:
        run()
    else:
        Printer().error('Python3.7+ needed.')
