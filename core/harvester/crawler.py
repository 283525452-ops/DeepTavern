# core/harvester/crawler.py
import requests
import time
import random
import trafilatura
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup # 我们需要把 BS4 请回来专门解析 Bing 的搜索结果页
from core.utils.logger import logger
import urllib3

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class WebCrawler:
    def __init__(self):
        # =====================================================
        # [可选] 如果你有代理 (如 v2ray/clash)，请在这里填入
        # 例如: proxies = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        # 如果没有代理，保持为 None 即可，代码会自动降级到 Bing
        # =====================================================
        self.proxies = None 
        
        try:
            self.ddgs = DDGS(proxy=self.proxies['http'] if self.proxies else None, timeout=10)
        except:
            self.ddgs = None

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }

    def _fetch_via_jina(self, url):
        """策略A: 使用 Jina Reader (抗反爬 + 转Markdown)"""
        jina_url = f"https://r.jina.ai/{url}"
        try:
            # Jina 需要访问外网，如果本地有代理最好加上，没有也能跑(Jina服务器在海外)
            resp = requests.get(jina_url, headers=self.headers, timeout=30)
            if resp.status_code == 200:
                text = resp.text
                if len(text) > 200 and "Cloudflare" not in text:
                    return text
        except Exception as e:
            logger.debug(f"[Crawler] Jina fetch failed: {e}")
        return None

    def _fetch_via_local(self, url):
        """策略B: 本地 Requests + Trafilatura (本地直连)"""
        try:
            resp = requests.get(url, headers=self.headers, timeout=15, verify=False)
            if resp.status_code == 200:
                # 自动修正编码
                if resp.encoding == 'ISO-8859-1':
                    resp.encoding = resp.apparent_encoding
                
                # 提取正文
                text = trafilatura.extract(
                    resp.text, 
                    include_comments=False, 
                    include_tables=True, 
                    include_formatting=True, # 保留 Markdown 格式
                    no_fallback=True
                )
                return text
        except Exception as e:
            logger.debug(f"[Crawler] Local fetch failed: {e}")
        return None

    def _search_ddg(self, keyword, max_results):
        """引擎 1: DuckDuckGo"""
        links = []
        if not self.ddgs: return []
        try:
            logger.info(f"[Crawler] 🔍 Searching via DuckDuckGo...")
            results = self.ddgs.text(keyword, region='cn-zh', max_results=max_results+2)
            for r in results:
                links.append({'href': r['href'], 'title': r['title']})
        except Exception as e:
            logger.warning(f"[Crawler] DDG failed (Network Issue?): {e}")
        return links

    def _search_bing(self, keyword, max_results):
        """引擎 2: Bing CN (国内直连)"""
        links = []
        try:
            logger.info(f"[Crawler] 🔍 Fallback to Bing CN...")
            url = f"https://cn.bing.com/search?q={keyword}"
            resp = requests.get(url, headers=self.headers, timeout=10, verify=False)
            
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                # 解析 Bing 的列表结构
                items = soup.find_all('li', class_='b_algo')
                for item in items:
                    h2 = item.find('h2')
                    if h2:
                        a_tag = h2.find('a')
                        if a_tag and a_tag.get('href'):
                            links.append({
                                'href': a_tag['href'],
                                'title': a_tag.get_text()
                            })
                            if len(links) >= max_results + 2: break
        except Exception as e:
            logger.error(f"[Crawler] Bing search failed: {e}")
        return links

    def search_and_fetch(self, keyword, whitelist=[], blacklist=[], max_results=3):
        # 1. 搜索阶段 (双引擎)
        search_links = self._search_ddg(keyword, max_results)
        
        # 如果 DDG 挂了，自动切换到 Bing
        if not search_links:
            search_links = self._search_bing(keyword, max_results)

        if not search_links:
            logger.warning("[Crawler] All search engines failed.")
            return []

        # 2. 筛选阶段
        candidates = []
        for item in search_links:
            url = item['href']
            # 简单的域名提取
            try:
                domain = url.split('/')[2]
            except:
                domain = ""
            
            if any(black in domain for black in blacklist): continue
            
            score = 50
            if any(white in domain for white in whitelist): score = 100
            
            candidates.append((score, item))
        
        candidates.sort(key=lambda x: x[0], reverse=True)
        targets = candidates[:max_results]
        
        logger.info(f"[Crawler] 🎯 Targets: {[t[1]['title'][:10] for t in targets]}")
        
        results = []

        # 3. 抓取阶段 (混合策略)
        for _, item in targets:
            url = item['href']
            title = item['title']
            domain = url.split('/')[2] if '//' in url else url
            
            time.sleep(random.uniform(1, 3))
            
            # 优先 Jina (云端)
            content = self._fetch_via_jina(url)
            source_type = "Jina-Reader"
            
            # 失败则本地 Trafilatura
            if not content:
                content = self._fetch_via_local(url)
                source_type = "Local-Trafilatura"

            if content and len(content) > 50: # 放宽限制，有些短设定也很有用
                logger.info(f"[Crawler] ✅ Fetched [{source_type}]: {title[:15]}... ({len(content)} chars)")
                results.append({
                    "title": title,
                    "url": url,
                    "content": content,
                    "domain": domain
                })
            else:
                logger.warning(f"[Crawler] ❌ Content empty: {url}")

        return results
