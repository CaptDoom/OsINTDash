import { Queue, Worker } from 'bullmq';
import { scrapeArticleText } from './scraper.js';
import { generateSummary } from './summarizer.js';
import Datastore from 'nedb-promises';
import path from 'path';
import { fileURLToPath } from 'url';
import os from 'os';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const dataDir = path.join(__dirname, '../data');

const articleDb = Datastore.create({
  filename: path.join(dataDir, 'articles.db'),
  autoload: true,
});

const jobsStore = new Map();
let scraperQueue = null;
let bullmqActive = false;

// Broadcast callback to let WebSocket server broadcast state updates
let onJobUpdate = () => {};

export const setJobUpdateCallback = (callback) => {
  onJobUpdate = callback;
};

const processJob = async (jobId, url, platform) => {
  try {
    console.log(`[Queue] Starting Job ${jobId} for: ${url}`);
    jobsStore.set(jobId, { url, platform, status: 'scraping', progress: 20 });
    onJobUpdate(jobId, 'scraping', 20);

    // 1. Scrape Content
    const scraped = await scrapeArticleText(url, platform);
    jobsStore.set(jobId, { url, platform, status: 'summarizing', progress: 50, title: scraped.title });
    onJobUpdate(jobId, 'summarizing', 50);

    // 2. Classify and Summarize
    const enrichment = await generateSummary(scraped.title, scraped.text);
    jobsStore.set(jobId, { url, platform, status: 'saving', progress: 80 });
    onJobUpdate(jobId, 'saving', 80);

    // Map AI category to dashboard category
    let category = 'Social';
    if (enrichment.domain === 'Geopolitics') category = 'Political';
    else if (enrichment.domain === 'Technology') category = 'Tech';
    else if (enrichment.domain === 'Finance') category = 'Economic';
    else if (enrichment.domain === 'Military' || enrichment.domain === 'Border') category = 'Military';

    // 3. Save to database
    const newArticle = {
      title: scraped.title,
      headline: scraped.title,
      summary: enrichment.summary,
      category: category,
      intel_category: enrichment.domain,
      source: platform.toUpperCase(),
      url,
      timestamp: new Date().toISOString(),
      relevance_score: 9.5,
      verification_status: 'AI Enriched',
      confidence_score: 0.98,
      country: 'Global' // Mark as global so it doesn't pollute specific border feeds unnecessarily unless tagged
    };

    const saved = await articleDb.insert(newArticle);
    
    // Inject into in-memory cached articles array in main app
    if (global.addArticleToInMemoryStore) {
      global.addArticleToInMemoryStore(saved);
    }

    jobsStore.set(jobId, { url, platform, status: 'completed', progress: 100, result: saved });
    onJobUpdate(jobId, 'completed', 100, saved);
    console.log(`[Queue] Completed Job ${jobId} successfully`);
    return saved;
  } catch (err) {
    console.error(`[Queue] Job ${jobId} failed: ${err.message}`);
    jobsStore.set(jobId, { url, platform, status: 'failed', progress: 100, error: err.message });
    onJobUpdate(jobId, 'failed', 100, null, err.message);
    throw err;
  }
};

const initQueue = () => {
  if (process.env.REDIS_URL) {
    try {
      const connectionOpts = {
        url: process.env.REDIS_URL,
        maxRetriesPerRequest: null
      };

      scraperQueue = new Queue('scrapingQueue', { connection: connectionOpts });
      
      const worker = new Worker('scrapingQueue', async (job) => {
        const { url, platform } = job.data;
        return await processJob(job.id, url, platform);
      }, { 
        connection: connectionOpts,
        concurrency: Math.max(1, Math.floor(os.cpus().length / 2))
      });

      worker.on('completed', (job) => {
        console.log(`[BullMQ] Worker job ${job.id} completed.`);
      });

      worker.on('failed', (job, err) => {
        console.error(`[BullMQ] Worker job ${job.id} failed: ${err.message}`);
      });

      bullmqActive = true;
      console.log('[BullMQ] Initialized successfully with Redis.');
    } catch (err) {
      console.warn('[BullMQ] Failed to initialize, using local in-memory worker fallback:', err.message);
    }
  } else {
    console.log('[Queue] No REDIS_URL provided. Operating in-memory background worker.');
  }
};

export const addScrapeJob = async (url, platform) => {
  const jobId = `job_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
  
  if (bullmqActive && scraperQueue) {
    try {
      await scraperQueue.add('scrapeTask', { url, platform }, { jobId });
      jobsStore.set(jobId, { url, platform, status: 'queued', progress: 10 });
      return jobId;
    } catch (err) {
      console.warn('[Queue] BullMQ push failed, falling back to local runner:', err.message);
    }
  }

  // In-memory queue fallback
  jobsStore.set(jobId, { url, platform, status: 'queued', progress: 10 });
  onJobUpdate(jobId, 'queued', 10);

  // Defer execution to background
  setTimeout(() => {
    processJob(jobId, url, platform).catch(() => {});
  }, 100);

  return jobId;
};

export const getJobStatus = (jobId) => {
  return jobsStore.get(jobId) || { status: 'not_found' };
};

initQueue();
