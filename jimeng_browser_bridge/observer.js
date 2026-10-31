(() => {
  if (window.__astrogateJimengObserverInstalled) {
    return;
  }
  window.__astrogateJimengObserverInstalled = true;

  const source = "astrogate-jimeng-observer";

  function emit(message) {
    window.postMessage({ source, ...message }, location.origin);
  }

  function requestPath(input) {
    try {
      const value = input instanceof Request ? input.url : input;
      return new URL(value, location.origin).pathname;
    } catch {
      return "";
    }
  }

  function resourceId(payload) {
    const result = payload?.data?.results?.find((item) => typeof item?.resource_id === "string");
    return result?.resource_id || null;
  }

  function permittedVideoUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && (url.hostname.endsWith(".jianying.com") || url.hostname.endsWith(".byteimg.com"));
    } catch {
      return false;
    }
  }

  function videos(payload) {
    const data = payload?.data;
    if (!data || !Array.isArray(data.resources) || !data.media_urls || typeof data.media_urls !== "object") {
      return [];
    }
    return data.resources.flatMap((resource) => {
      const resourceIdValue = resource?.resource_id;
      const mediaId = resource?.video?.vid;
      const media = typeof mediaId === "string" ? data.media_urls[mediaId] : null;
      const videoUrl = media?.download_url || media?.url;
      if (typeof resourceIdValue !== "string" || !permittedVideoUrl(videoUrl)) {
        return [];
      }
      return [{ resourceId: resourceIdValue, videoUrl }];
    });
  }

  function inspect(path, response) {
    if (!path.endsWith("/node_generate") && !path.endsWith("/resource/batch_get")) {
      return;
    }
    response.clone().json().then((payload) => {
      if (path.endsWith("/node_generate")) {
        const id = resourceId(payload);
        if (id) {
          emit({ type: "resource", resourceId: id });
        }
        return;
      }
      const results = videos(payload);
      if (results.length) {
        emit({ type: "video", results: results.slice(0, 8) });
      }
    }).catch(() => {});
  }

  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const response = await originalFetch.apply(this, args);
    inspect(requestPath(args[0]), response);
    return response;
  };
})();
