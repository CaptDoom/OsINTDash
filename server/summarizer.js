const domainPrompts = {
  Geopolitics: "Focus on key stakeholders, territorial implications, national security relevance, and diplomatic stances. Structure it with clear, professional language.",
  Technology: "Highlight technical specifications, architectures, developers/companies, and market disruptors or innovations.",
  Finance: "Extract primary market indicators, stock price movements, transaction values, and investor sentiment.",
  Health: "Outline clinical trial results, study sample sizes, medical specs, and public health/safety advisories.",
  General: "Outline a clear 3-bullet factual summary covering Who, What, Where, and Why."
};

const chunkText = (text, maxLength = 8000) => {
  const words = text.split(/\s+/);
  const chunks = [];
  let current = [];
  let currentLength = 0;

  for (const word of words) {
    if (currentLength + word.length > maxLength) {
      chunks.push(current.join(' '));
      current = [word];
      currentLength = word.length;
    } else {
      current.push(word);
      currentLength += word.length + 1;
    }
  }
  if (current.length) {
    chunks.push(current.join(' '));
  }
  return chunks;
};

const callLLM = async (prompt, systemInstruction = "You are an intelligence summarizer.") => {
  const provider = (process.env.LLM_PROVIDER || (process.env.OPENAI_API_KEY ? 'openai' : process.env.HF_API_KEY ? 'huggingface' : 'ollama')).toLowerCase();
  
  if (provider === 'openai' && process.env.OPENAI_API_KEY) {
    try {
      const response = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.OPENAI_API_KEY}`
        },
        body: JSON.stringify({
          model: process.env.LLM_MODEL || 'gpt-4o-mini',
          messages: [
            { role: 'system', content: systemInstruction },
            { role: 'user', content: prompt }
          ],
          temperature: 0.2
        })
      });
      if (!response.ok) throw new Error(`OpenAI responded with ${response.status}`);
      const data = await response.json();
      return data.choices[0].message.content.trim();
    } catch (err) {
      console.warn(`[Summarizer] OpenAI failed: ${err.message}. Falling back to Ollama.`);
    }
  }

  // Ollama Option
  if (provider === 'ollama' || !process.env.OPENAI_API_KEY) {
    const baseUrl = process.env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434';
    const model = process.env.LLM_MODEL || 'llama3.1:8b-instruct';
    try {
      const response = await fetch(`${baseUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          messages: [
            { role: 'system', content: systemInstruction },
            { role: 'user', content: prompt }
          ],
          stream: false,
          options: { temperature: 0.2 }
        })
      });
      if (!response.ok) throw new Error(`Ollama responded with ${response.status}`);
      const data = await response.json();
      return data.message.content.trim();
    } catch (err) {
      console.warn(`[Summarizer] Ollama failed: ${err.message}. Trying Hugging Face.`);
    }
  }

  // Hugging Face Option
  if (process.env.HF_API_KEY) {
    const model = process.env.HF_MODEL || 'google/flan-t5-large';
    try {
      const response = await fetch(`https://api-inference.huggingface.co/models/${encodeURIComponent(model)}`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${process.env.HF_API_KEY}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          inputs: `${systemInstruction}\n\nQuery: ${prompt}`,
          parameters: { max_new_tokens: 300, temperature: 0.2 }
        })
      });
      if (!response.ok) throw new Error(`HF responded with ${response.status}`);
      const data = await response.json();
      const output = Array.isArray(data) ? (data[0]?.generated_text || '') : (data?.generated_text || '');
      if (output.trim()) return output.trim();
    } catch (err) {
      console.warn(`[Summarizer] Hugging Face failed: ${err.message}`);
    }
  }

  // Heuristic Fallback summary
  console.log('[Summarizer] Using heuristic rule-based summarization.');
  const lines = prompt.split('\n').filter(l => l.trim().length > 40);
  const coreParagraph = lines[0] || 'No summary could be dynamically generated.';
  return `[Heuristic Summary] ${coreParagraph.slice(0, 250)}...`;
};

export const classifyDomain = async (title, text) => {
  const prompt = `Classify this article into exactly one of: Geopolitics, Technology, Finance, Health, General. Do not output anything other than the single word.\n\nTitle: ${title}\nText segment: ${text.slice(0, 1500)}`;
  try {
    const rawClass = await callLLM(prompt, "You are a classifier. Respond with exactly one category name.");
    const cleaned = rawClass.replace(/[^a-zA-Z]/g, '').trim();
    if (Object.keys(domainPrompts).includes(cleaned)) {
      return cleaned;
    }
  } catch (err) {
    console.warn(`[Summarizer] Domain classification failed: ${err.message}`);
  }
  return 'General';
};

export const generateSummary = async (title, text) => {
  const domain = await classifyDomain(title, text);
  const directive = domainPrompts[domain] || domainPrompts.General;

  let summarizedText = text;
  if (text.length > 10000) {
    console.log(`[Summarizer] Running Map-Reduce for long content of size ${text.length} chars...`);
    const chunks = chunkText(text, 8000);
    const summaries = [];
    for (let i = 0; i < chunks.length; i++) {
      const chunkSum = await callLLM(
        `Summarize the key factual details in this section of the article:\n\n${chunks[i]}`,
        "You are an assistant summarizing a document chunk."
      );
      summaries.push(chunkSum);
    }
    summarizedText = summaries.join('\n\n');
  }

  const summary = await callLLM(
    `Article Title: ${title}\n\nContent:\n${summarizedText}`,
    `You are a Senior Full-Stack Architect AI Summarizer. Instructions: ${directive}`
  );

  return {
    domain,
    summary
  };
};
