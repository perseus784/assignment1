// Client for the Trailside engine server.

export class EngineClient {
  constructor(baseUrl) {
    this.base = (baseUrl || '').replace(/\/?$/, '/');
  }

  async health() {
    const res = await fetch(`${this.base}v1/health`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Engine unavailable');
    return res.json();
  }

  /**
   * Ask the engine to build a tour and wait for it.
   * @param {object} body {query} | {center:[lat,lon], radius_m} | {route:[[lat,lon],...]}
   * @param {(job: object) => void} onProgress
   */
  async buildTour(body, onProgress = () => {}, { signal } = {}) {
    const res = await fetch(`${this.base}v1/tours`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail?.[0]?.msg ?? `Engine error ${res.status}`);
    let job = await res.json();
    onProgress(job);
    while (job.status === 'queued' || job.status === 'running') {
      await new Promise((r) => setTimeout(r, 700));
      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      const poll = await fetch(`${this.base}v1/jobs/${job.id}`, { cache: 'no-store', signal });
      job = await poll.json();
      onProgress(job);
    }
    if (job.status !== 'done') throw new Error(job.error || 'The engine could not build this tour');
    return job;
  }
}
