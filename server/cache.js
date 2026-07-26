import Redis from 'ioredis';

let redis = null;
const memoryCache = new Map();

if (process.env.REDIS_URL) {
  try {
    redis = new Redis(process.env.REDIS_URL, {
      maxRetriesPerRequest: 1,
      connectTimeout: 2000,
    });
    redis.on('error', (err) => {
      console.warn('[Redis] Client connection error, using in-memory fallback.');
    });
  } catch (err) {
    console.warn('[Redis] Failed to initialize client, using in-memory cache.');
  }
} else {
  console.log('[Redis] No REDIS_URL configured. Caching is in-memory.');
}

export const getCache = async (key) => {
  if (redis && redis.status === 'ready') {
    try {
      return await redis.get(key);
    } catch (err) {
      // Fall through to memoryCache
    }
  }
  const item = memoryCache.get(key);
  if (item) {
    if (item.expiry > Date.now()) {
      return item.value;
    }
    memoryCache.delete(key);
  }
  return null;
};

export const setCache = async (key, value, ttlSeconds) => {
  if (redis && redis.status === 'ready') {
    try {
      await redis.setex(key, ttlSeconds, value);
      return;
    } catch (err) {
      // Fall through to memoryCache
    }
  }
  memoryCache.set(key, {
    value,
    expiry: Date.now() + (ttlSeconds * 1000),
  });
};

// Stale-While-Revalidate (SWR) dynamic fetch-caching
export const getOrSetCacheSWR = async (key, fetchFunction, ttlSeconds) => {
  const cached = await getCache(key);

  if (cached) {
    try {
      const parsed = JSON.parse(cached);
      const age = (Date.now() - parsed.cachedAt) / 1000;

      // If the cache item is stale, trigger a background refresh
      if (age > ttlSeconds) {
        console.log(`[Cache SWR] Key ${key} is stale (Age: ${age.toFixed(1)}s > TTL: ${ttlSeconds}s). Revalidating...`);
        fetchFunction().then(async (freshData) => {
          const payload = {
            cachedAt: Date.now(),
            data: freshData
          };
          // Cache with double the TTL for SWR buffer padding
          await setCache(key, JSON.stringify(payload), ttlSeconds * 2);
          console.log(`[Cache SWR] Revalidated key ${key} successfully.`);
        }).catch((err) => {
          console.warn(`[Cache SWR] Background revalidation failed for ${key}: ${err.message}`);
        });
      }

      return parsed.data;
    } catch (err) {
      console.warn(`[Cache SWR] Parsing failed for cached key ${key}: ${err.message}`);
    }
  }

  // Cache miss: execute fetch synchronously
  console.log(`[Cache SWR] Cache miss for ${key}. Fetching fresh...`);
  const freshData = await fetchFunction();
  const payload = {
    cachedAt: Date.now(),
    data: freshData
  };
  await setCache(key, JSON.stringify(payload), ttlSeconds * 2);
  return freshData;
};
