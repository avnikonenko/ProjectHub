/** Poll one session at a time; cleanup prevents late work from updating its view. */
export function startReviewPolling<T>(
  load: (signal: AbortSignal) => Promise<T>,
  onResult: (value: T) => void,
  onError: (error: unknown) => void,
  delayMs = 3000,
): () => void {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function poll() {
    try {
      const value = await load(controller.signal);
      if (!controller.signal.aborted) onResult(value);
    } catch (error) {
      if (!controller.signal.aborted) onError(error);
    } finally {
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, delayMs);
    }
  }
  void poll();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}
