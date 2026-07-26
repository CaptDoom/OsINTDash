import * as cheerio from 'cheerio';
import { Readability } from '@mozilla/readability';
import { JSDOM } from 'jsdom';

let browserInstance = null;
let browserFailed = false;

// Singleton browser pool launcher
const getBrowser = async () => {
  if (browserFailed) return null;
  if (browserInstance) return browserInstance;

  try {
    console.log('[Scraper Pool] Launching headless browser singleton...');
    const { chromium } = await import('playwright');
    browserInstance = await chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    });
    return browserInstance;
  } catch (err) {
    console.warn(`[Scraper Pool] Playwright browser launch failed: ${err.message}. Using HTTP/cheerio fallback.`);
    browserFailed = true; // Avoid retrying broken launches
    return null;
  }
};

export const scrapeArticleText = async (url, platform) => {
  let html = '';
  const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

  // 1. Check if we should route via ScraperAPI
  if (process.env.SCRAPER_API_KEY && (platform === 'twitter' || platform === 'x' || platform === 'reddit' || platform === 'linkedin')) {
    try {
      console.log(`[Scraper] Routing ${url} via ScraperAPI...`);
      const response = await fetch(`https://api.scraperapi.com/?api_key=${process.env.SCRAPER_API_KEY}&url=${encodeURIComponent(url)}`);
      if (response.ok) {
        html = await response.text();
      } else {
        throw new Error(`Status ${response.status}`);
      }
    } catch (err) {
      console.warn(`[Scraper] ScraperAPI call failed: ${err.message}`);
    }
  }

  // 2. If no proxy scrape succeeded, attempt browser pooling
  if (!html) {
    const browser = await getBrowser();
    if (browser) {
      let context = null;
      try {
        console.log(`[Scraper Pool] Spawning isolated context for: ${url}`);
        context = await browser.newContext({ userAgent });
        const page = await context.newPage();
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
        html = await page.content();
      } catch (err) {
        console.warn(`[Scraper Pool] Browser context load failed: ${err.message}`);
      } finally {
        if (context) {
          await context.close(); // Clean up context tab, release RAM
        }
      }
    }
  }

  // 3. Fallback to direct HTTP fetch
  if (!html) {
    console.log(`[Scraper] Fetching directly: ${url}`);
    const response = await fetch(url, { headers: { 'User-Agent': userAgent } });
    if (!response.ok) throw new Error(`Direct fetch failed with status: ${response.status}`);
    html = await response.text();
  }

  if (!html || html.trim() === '') {
    throw new Error('Retrieved HTML content is empty.');
  }

  // 4. Parse content using Readability
  try {
    const doc = new JSDOM(html, { url });
    const reader = new Readability(doc.window.document);
    const parsed = reader.parse();

    if (parsed && parsed.textContent && parsed.textContent.trim().length > 100) {
      return {
        title: parsed.title || 'Untitled Scraped Article',
        text: parsed.textContent.trim(),
        excerpt: parsed.excerpt || ''
      };
    }
  } catch (parseErr) {
    console.warn(`[Scraper] Readability parsing failed: ${parseErr.message}`);
  }

  // 5. Fallback to Cheerio paragraph selector extraction
  const $ = cheerio.load(html);
  $('script, style, nav, footer, header, noscript, iframe, svg').remove();
  
  const paragraphs = [];
  $('p').each((i, el) => {
    const pText = $(el).text().trim();
    if (pText.length > 40) {
      paragraphs.push(pText);
    }
  });

  const bodyText = paragraphs.join('\n\n');
  if (bodyText.length < 100) {
    throw new Error('Failed to extract meaningful text from webpage.');
  }

  const extractedTitle = $('title').text().trim() || $('h1').first().text().trim() || 'Scraped Article';
  
  return {
    title: extractedTitle,
    text: bodyText,
    excerpt: bodyText.slice(0, 200) + '...'
  };
};
