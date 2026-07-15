// Test script to verify the API keys in the environment using native fetch

async function testAPIs() {
  const NEWS_API_KEY = 'cf0bc88ad9dd4635bfb29f041188a429';
  const GNEWS_API_KEY = '3278839cd59842a8a3becda94aea428f';
  const THENEWS_API_KEY = '3278839cd59842a8a3becda94aea428f';

  // 1. NewsAPI
  try {
    const q = 'China military';
    const url = `https://newsapi.org/v2/everything?q=${encodeURIComponent(q)}&pageSize=2&apiKey=${NEWS_API_KEY}`;
    const res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    console.log('NewsAPI status:', res.status);
    const json = await res.json();
    console.log('NewsAPI articles found:', json.articles?.length || 0);
  } catch (err) {
    console.error('NewsAPI error:', err.message);
  }

  // 2. GNews
  try {
    const q = 'China military';
    const url = `https://gnews.io/api/v4/search?q=${encodeURIComponent(q)}&lang=en&max=2&apikey=${GNEWS_API_KEY}`;
    const res = await fetch(url);
    console.log('GNews status:', res.status);
    const json = await res.json();
    console.log('GNews articles found:', json.articles?.length || 0);
  } catch (err) {
    console.error('GNews error:', err.message);
  }

  // 3. TheNewsAPI
  try {
    const q = 'China military';
    const url = `https://api.thenewsapi.com/v1/news/all?search=${encodeURIComponent(q)}&language=en&limit=2&api_token=${THENEWS_API_KEY}`;
    const res = await fetch(url);
    console.log('TheNewsAPI status:', res.status);
    const json = await res.json();
    console.log('TheNewsAPI articles found:', json.data?.length || 0);
  } catch (err) {
    console.error('TheNewsAPI error:', err.message);
  }
}

testAPIs();
