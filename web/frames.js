/* The worker half of taking encoded video out of WebRTC.
 *
 * A browser will not hand a page the encoded frames on a media track from the
 * main thread: Safari's RTCRtpScriptTransform takes a Worker and nothing else,
 * and Chrome's newer builds agree. So this exists to be that worker, and it is
 * deliberately almost empty -- it reads frames and posts them on. Decoding
 * happens where the canvas is.
 *
 * Nothing is written back to `writable`. That is the point rather than an
 * omission: what is not written back never reaches the browser's own decoder
 * and its jitter buffer, which is the machinery being replaced. The <video>
 * element goes black, and the canvas beside it is what anybody sees.
 */
/* Registered before anything is told this worker exists.
 *
 * `new Worker()` returns before the worker's script has run, and attaching a
 * transform to a receiver on the next line is a race: if the rtctransform
 * event fires before this handler is set, it is simply lost, and the page sits
 * there having been handed no frames at all. Measured exactly that way -- "0
 * fed to the decoder" over ten seconds while twenty megabytes arrived. So the
 * page waits for the "ready" below before it attaches anything. */
self.onrtctransform = (event) => {
  const transformer = event.transformer;
  const reader = transformer.readable.getReader();

  const pump = () => reader.read().then(({ done, value }) => {
    if (done) return;
    const frame = value;
    const data = frame.data;
    // Copied out of the frame, not referenced: the frame is recycled the
    // moment this returns and a detached ArrayBuffer arrives as an empty
    // picture with no error anywhere.
    const bytes = new Uint8Array(data.byteLength);
    bytes.set(new Uint8Array(data));
    self.postMessage({
      bytes: bytes.buffer,
      timestamp: frame.timestamp,
      type: frame.type || "delta",
      at: performance.now(),
    }, [bytes.buffer]);
    pump();
  }).catch(() => { /* the connection went away */ });

  pump();
};

// Last, so it cannot be sent before the handler above exists.
self.postMessage({ ready: true });
