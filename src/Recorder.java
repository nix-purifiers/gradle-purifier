// SPDX-License-Identifier: MIT

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.Writer;
import java.net.Authenticator;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.PasswordAuthentication;
import java.net.ProxySelector;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The download recorder of a gradle-purifier lock run.
 *
 * <p>The init script points every remote repository of the build at this server: the URL
 * https://host/path becomes http://127.0.0.1:port/https/host/path. The server fetches the
 * remote URL and passes the response on. For every request the remote answers with 200, it
 * appends a line to the records file: the path of the file in the tree of locked downloads, the
 * remote URL, the SRI SHA-256 hash of the body, and a mirror URL that serves the same file, or
 * nothing, separated by tabs. For a file whose remote contents change over time (see {@link
 * #isMutable}), the line holds "inline" and the contents in Base64 instead of the URL and the
 * hash. A file that the lock pins (see {@link #readPinned}) is served and recorded from the
 * pinned contents, without a request to the remote.
 *
 * <p>Usage: {@code java Recorder <records file> <port file> [<tunnel host>...]}. The server
 * listens on an ephemeral port of the loopback address, and an HTTPS proxy that tunnels to the
 * tunnel hosts only listens on another (see {@link #startTunnel}). Once both are ready, it writes
 * the two ports to the port file, separated by a space. It runs until it is killed.
 *
 * <p>The server forwards the Authorization header, so a lock run can read a repository that
 * needs credentials. It takes its proxy from the https_proxy or HTTPS_PROXY environment
 * variable when one is set.
 */
public final class Recorder {

  private static final Set<String> FORWARDED_REQUEST_HEADERS =
      Set.of("accept", "authorization", "user-agent");

  private static final List<String> FORWARDED_RESPONSE_HEADERS =
      List.of("Content-Type", "Last-Modified", "ETag", "X-Checksum-Sha1", "X-Checksum-Sha256");

  private static final List<String> CHECKSUM_SUFFIXES =
      List.of(".sha1", ".sha256", ".sha512", ".md5");

  /** The URL prefixes of Maven Central, and Google's mirror of it, which serves the same files. */
  private static final List<String> CENTRAL =
      List.of("https://repo.maven.apache.org/maven2/", "https://repo1.maven.org/maven2/");

  private static final String CENTRAL_MIRROR =
      "https://maven-central.storage-download.googleapis.com/maven2/";

  /**
   * Whether the recorder fetches Maven Central's files from its mirror: when the environment sets
   * GRADLE_PURIFIER_CENTRAL_MIRROR to 1. The lockfile names Maven Central either way.
   */
  private static final boolean FETCH_FROM_MIRROR =
      "1".equals(System.getenv("GRADLE_PURIFIER_CENTRAL_MIRROR"));

  /** The digest algorithm of each checksum suffix. */
  private static final java.util.Map<String, String> CHECKSUM_ALGORITHMS =
      java.util.Map.of(".sha1", "SHA-1", ".sha256", "SHA-256", ".sha512", "SHA-512", ".md5", "MD5");

  private final HttpClient client;
  private final Writer records;
  private final java.util.Map<String, byte[]> pinned;

  private Recorder(HttpClient client, Writer records, java.util.Map<String, byte[]> pinned) {
    this.client = client;
    this.records = records;
    this.pinned = pinned;
  }

  /**
   * The pinned files of the file that the environment variable GRADLE_PURIFIER_PINNED names, by
   * their path in the tree of locked downloads. Each line of the file holds a path and the
   * contents in Base64, separated by a tab. `gradle-purifier lock` pins the version listings that
   * the lockfile holds, so that a relock resolves every dynamic version as the lock did.
   */
  private static java.util.Map<String, byte[]> readPinned() throws IOException {
    String file = System.getenv("GRADLE_PURIFIER_PINNED");
    java.util.Map<String, byte[]> pinned = new java.util.HashMap<>();
    if (file == null || file.isEmpty()) {
      return pinned;
    }
    for (String line : Files.readAllLines(Path.of(file), StandardCharsets.UTF_8)) {
      int tab = line.indexOf('\t');
      if (tab > 0) {
        pinned.put(line.substring(0, tab), Base64.getDecoder().decode(line.substring(tab + 1)));
      }
    }
    return pinned;
  }

  /**
   * The contents the recorder serves at `path` from the pinned files, or null when the remote
   * serves it. A checksum file of a pinned file, such as maven-metadata.xml.sha1, is the
   * checksum of the pinned contents in lowercase hexadecimal, so that it matches them.
   */
  static byte[] pinnedContents(java.util.Map<String, byte[]> pinned, String path) {
    byte[] contents = pinned.get(path);
    if (contents != null) {
      return contents;
    }
    for (java.util.Map.Entry<String, String> checksum : CHECKSUM_ALGORITHMS.entrySet()) {
      String suffix = checksum.getKey();
      if (path.endsWith(suffix)) {
        byte[] described = pinned.get(path.substring(0, path.length() - suffix.length()));
        if (described == null) {
          return null;
        }
        try {
          return java.util.HexFormat.of()
              .formatHex(MessageDigest.getInstance(checksum.getValue()).digest(described))
              .getBytes(StandardCharsets.US_ASCII);
        } catch (NoSuchAlgorithmException e) {
          throw new IllegalStateException(e);
        }
      }
    }
    return null;
  }

  public static void main(String[] args) throws IOException {
    if (args.length < 2) {
      System.err.println("usage: java Recorder <records file> <port file> [<tunnel host>...]");
      System.exit(2);
    }
    Path recordsFile = Path.of(args[0]);
    Path portFile = Path.of(args[1]);
    Set<String> tunnelHosts = Set.of(Arrays.copyOfRange(args, 2, args.length));

    HttpClient.Builder builder =
        HttpClient.newBuilder()
            // get() follows redirects itself.
            .followRedirects(HttpClient.Redirect.NEVER)
            .connectTimeout(Duration.ofSeconds(30));
    URI proxy = proxyFromEnvironment();
    if (proxy != null) {
      builder.proxy(
          ProxySelector.of(
              new InetSocketAddress(proxy.getHost(), proxy.getPort() == -1 ? 80 : proxy.getPort())));
      String userInfo = proxy.getRawUserInfo();
      if (userInfo != null) {
        // The credentials of a proxy URL such as http://user:password@host:port. Java sends
        // them through an HTTPS tunnel only when the command clears
        // jdk.http.auth.tunneling.disabledSchemes, which it does.
        int colon = userInfo.indexOf(':');
        String user = decode(colon == -1 ? userInfo : userInfo.substring(0, colon));
        char[] password = decode(colon == -1 ? "" : userInfo.substring(colon + 1)).toCharArray();
        builder.authenticator(
            new Authenticator() {
              @Override
              protected PasswordAuthentication getPasswordAuthentication() {
                return getRequestorType() == RequestorType.PROXY
                    ? new PasswordAuthentication(user, password)
                    : null;
              }
            });
      }
    }

    Writer records =
        Files.newBufferedWriter(
            recordsFile,
            StandardCharsets.UTF_8,
            StandardOpenOption.CREATE,
            StandardOpenOption.APPEND);
    Recorder recorder = new Recorder(builder.build(), records, readPinned());

    HttpServer server =
        HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
    server.setExecutor(Executors.newFixedThreadPool(32));
    server.createContext("/", recorder::handle);
    server.start();

    ServerSocket tunnel = startTunnel(tunnelHosts, proxy);

    Path tmp = Files.createTempFile(portFile.toAbsolutePath().getParent(), "port", ".tmp");
    Files.writeString(tmp, server.getAddress().getPort() + " " + tunnel.getLocalPort() + "\n");
    Files.move(tmp, portFile, StandardCopyOption.ATOMIC_MOVE);
  }

  /**
   * Starts an HTTPS proxy on an ephemeral port of the loopback address that tunnels to the hosts
   * in `hosts` and refuses every other host. It tunnels through `upstream` when that is not
   * null. A downloader that uses the proxy settings of the JVM, such as the one in Kotlin/Native,
   * reaches its own host through it. The tunnel carries TLS, so the command records what such a
   * downloader fetched from the directory where the downloader keeps it.
   */
  private static ServerSocket startTunnel(Set<String> hosts, URI upstream) throws IOException {
    ServerSocket socket = new ServerSocket(0, 50, InetAddress.getLoopbackAddress());
    ExecutorService pool = Executors.newCachedThreadPool();
    Thread acceptor =
        new Thread(
            () -> {
              while (true) {
                try {
                  Socket client = socket.accept();
                  pool.execute(() -> tunnel(client, hosts, upstream));
                } catch (IOException e) {
                  System.err.println("gradle-purifier recorder: tunnel: " + e);
                  return;
                }
              }
            },
            "tunnel");
    acceptor.start();
    return socket;
  }

  private static void tunnel(Socket client, Set<String> hosts, URI upstream) {
    try (client) {
      InputStream fromClient = client.getInputStream();
      OutputStream toClient = client.getOutputStream();
      String[] request = readHead(fromClient).split("\r\n", 2)[0].split(" ");
      if (request.length < 2 || !request[0].equals("CONNECT")) {
        toClient.write(status("405 Method Not Allowed"));
        return;
      }
      String target = request[1];
      int colon = target.lastIndexOf(':');
      String host = colon == -1 ? target : target.substring(0, colon);
      int port = colon == -1 ? 443 : Integer.parseInt(target.substring(colon + 1));
      if (!hosts.contains(host)) {
        System.err.println("gradle-purifier recorder: refused a tunnel to " + target);
        toClient.write(status("403 Forbidden"));
        return;
      }
      try (Socket remote = connect(host, port, upstream)) {
        if (remote == null) {
          toClient.write(status("502 Bad Gateway"));
          return;
        }
        toClient.write(status("200 Connection Established"));
        toClient.flush();
        Thread up =
            new Thread(
                () -> {
                  try {
                    fromClient.transferTo(remote.getOutputStream());
                    remote.shutdownOutput();
                  } catch (IOException e) {
                    // The other direction ends the tunnel.
                  }
                });
        up.start();
        remote.getInputStream().transferTo(toClient);
        up.join();
      }
    } catch (IOException e) {
      System.err.println("gradle-purifier recorder: tunnel: " + e);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    }
  }

  /**
   * A connection to host:port, directly or through the CONNECT method of `upstream`; null when
   * the upstream proxy refuses.
   */
  private static Socket connect(String host, int port, URI upstream) throws IOException {
    if (upstream == null) {
      return new Socket(host, port);
    }
    Socket socket =
        new Socket(upstream.getHost(), upstream.getPort() == -1 ? 80 : upstream.getPort());
    StringBuilder request = new StringBuilder();
    request.append("CONNECT ").append(host).append(':').append(port).append(" HTTP/1.1\r\n");
    request.append("Host: ").append(host).append(':').append(port).append("\r\n");
    String userInfo = upstream.getRawUserInfo();
    if (userInfo != null) {
      String credentials =
          Base64.getEncoder().encodeToString(decode(userInfo).getBytes(StandardCharsets.UTF_8));
      request.append("Proxy-Authorization: Basic ").append(credentials).append("\r\n");
    }
    request.append("\r\n");
    socket.getOutputStream().write(request.toString().getBytes(StandardCharsets.ISO_8859_1));
    socket.getOutputStream().flush();
    String reply = readHead(socket.getInputStream());
    String[] statusLine = reply.split("\r\n", 2)[0].split(" ");
    if (statusLine.length < 2 || !statusLine[1].equals("200")) {
      System.err.println("gradle-purifier recorder: the proxy refused a tunnel to " + host + ": " + reply);
      socket.close();
      return null;
    }
    return socket;
  }

  /** Reads an HTTP message head, up to and without the empty line that ends it. */
  private static String readHead(InputStream in) throws IOException {
    StringBuilder head = new StringBuilder();
    int c;
    while ((c = in.read()) != -1) {
      head.append((char) c);
      if (head.length() >= 4 && head.substring(head.length() - 4).equals("\r\n\r\n")) {
        return head.substring(0, head.length() - 4);
      }
    }
    return head.toString();
  }

  private static byte[] status(String status) {
    return ("HTTP/1.1 " + status + "\r\nContent-Length: 0\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1);
  }

  private static String decode(String text) {
    return URLDecoder.decode(text, StandardCharsets.UTF_8);
  }

  private static URI proxyFromEnvironment() {
    for (String name : List.of("https_proxy", "HTTPS_PROXY")) {
      String value = System.getenv(name);
      if (value != null && !value.isEmpty()) {
        return URI.create(value.contains("://") ? value : "http://" + value);
      }
    }
    return null;
  }

  /**
   * The remote URL of a request path such as /https/repo.maven.apache.org/maven2/x, or null when
   * the path does not name one.
   */
  static URI remoteOf(String rawPath) {
    String[] parts = rawPath.split("/", 4);
    // parts[0] is the empty text before the leading slash.
    if (parts.length < 4 || !(parts[1].equals("http") || parts[1].equals("https"))) {
      return null;
    }
    return URI.create(parts[1] + "://" + parts[2] + "/" + parts[3]);
  }

  /** The mirror URL of a Maven Central URL, or null for any other URL. */
  static URI mirrorOf(URI remote) {
    String url = remote.toString();
    for (String prefix : CENTRAL) {
      if (url.startsWith(prefix)) {
        return URI.create(CENTRAL_MIRROR + url.substring(prefix.length()));
      }
    }
    return null;
  }

  /** The URL the recorder fetches `remote` from: its mirror when it fetches from mirrors. */
  private static URI source(URI remote) {
    URI mirror = mirrorOf(remote);
    return FETCH_FROM_MIRROR && mirror != null ? mirror : remote;
  }

  /** The path of a remote file in the tree of locked downloads. */
  static String treePathOf(URI remote) {
    String authority = remote.getPort() == -1 ? remote.getHost() : remote.getHost() + ":" + remote.getPort();
    return remote.getScheme() + "/" + authority + remote.getPath();
  }

  private void handle(HttpExchange exchange) throws IOException {
    try {
      forward(exchange);
    } finally {
      exchange.close();
    }
  }

  private void forward(HttpExchange exchange) throws IOException {
    String method = exchange.getRequestMethod();
    URI remote = remoteOf(exchange.getRequestURI().getRawPath());
    if (remote == null || !(method.equals("GET") || method.equals("HEAD"))) {
      exchange.sendResponseHeaders(remote == null ? 404 : 405, -1);
      return;
    }

    String path = treePathOf(remote);
    byte[] held = pinnedContents(pinned, path);
    if (held != null) {
      exchange.getResponseHeaders().add("Content-Length", Long.toString(held.length));
      if (method.equals("HEAD")) {
        exchange.sendResponseHeaders(200, -1);
      } else {
        exchange.sendResponseHeaders(200, held.length > 0 ? held.length : -1);
        try (OutputStream out = exchange.getResponseBody()) {
          out.write(held);
        }
      }
      record(path, "inline", Base64.getEncoder().encodeToString(held), "");
      return;
    }

    // A HEAD goes to the remote as a GET. Gradle sends HEAD to compare a
    // remote file's checksum headers with a file it already holds from
    // another repository, and reuses that file when they match. The lock
    // must hold the file at this repository's path as well, or the
    // sandboxed build, which reads only this path, does not find it.
    java.util.Map<String, List<String>> headers = new java.util.HashMap<>();
    exchange
        .getRequestHeaders()
        .forEach(
            (name, values) -> {
              if (FORWARDED_REQUEST_HEADERS.contains(name.toLowerCase())) {
                headers.put(name, values);
              }
            });

    HttpResponse<InputStream> response;
    try {
      response = get(remote, headers);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      exchange.sendResponseHeaders(502, -1);
      return;
    } catch (IOException e) {
      System.err.println("gradle-purifier recorder: " + remote + ": " + e);
      exchange.sendResponseHeaders(502, -1);
      return;
    }

    for (String name : FORWARDED_RESPONSE_HEADERS) {
      response.headers().firstValue(name).ifPresent(v -> exchange.getResponseHeaders().add(name, v));
    }
    int status = response.statusCode();
    long length = response.headers().firstValueAsLong("Content-Length").orElse(-1);

    MessageDigest sha256 = sha256();
    ByteArrayOutputStream contents =
        isMutable(remote) || isJavadocMetadata(remote, exchange.getRequestHeaders().getFirst("X-Gradle-Purifier-Resource"))
            ? new ByteArrayOutputStream() : null;
    try (InputStream body = response.body()) {
      if (method.equals("HEAD")) {
        long read = drain(body, sha256, contents);
        exchange.getResponseHeaders().add("Content-Length", Long.toString(length >= 0 ? length : read));
        exchange.sendResponseHeaders(status, -1);
      } else {
        // A length of 0 makes the server send the body in chunks, and -1 sends no body.
        exchange.sendResponseHeaders(status, length > 0 ? length : (length == 0 ? -1 : 0));
        try (OutputStream out = exchange.getResponseBody()) {
          drain(body, sha256, contents == null ? out : new TeeOutputStream(out, contents));
        }
      }
    }
    if (status == 200) {
      record(remote, sha256, contents);
      // Gradle may also compare a checksum file with a file it already
      // holds, and then fetches only the checksum file; the file it
      // describes is recorded too.
      URI described = describedBy(remote);
      if (described != null) {
        recordFetched(described);
      }
    }
  }

  /** The file a checksum file such as x.jar.sha1 describes, or null for any other file. */
  static URI describedBy(URI remote) {
    String path = remote.getRawPath();
    for (String suffix : CHECKSUM_SUFFIXES) {
      if (path.endsWith(suffix)) {
        return remote.resolve(path.substring(path.lastIndexOf('/') + 1, path.length() - suffix.length()));
      }
    }
    return null;
  }

  /**
   * GETs `remote` from where the recorder fetches it, following redirects itself: a redirect to
   * Maven Central goes to its mirror too, when the recorder fetches from mirrors (the plugin
   * portal redirects many of its files to Maven Central).
   */
  private HttpResponse<InputStream> get(URI remote, java.util.Map<String, List<String>> headers)
      throws IOException, InterruptedException {
    URI target = source(remote);
    for (int redirects = 0; ; redirects++) {
      HttpRequest.Builder request =
          HttpRequest.newBuilder(target).timeout(Duration.ofMinutes(10)).GET();
      headers.forEach((name, values) -> values.forEach(v -> request.header(name, v)));
      HttpResponse<InputStream> response =
          client.send(request.build(), HttpResponse.BodyHandlers.ofInputStream());
      int status = response.statusCode();
      java.util.Optional<String> location = response.headers().firstValue("Location");
      if (status < 300 || status >= 400 || status == 304 || location.isEmpty() || redirects >= 10) {
        return response;
      }
      response.body().close();
      target = source(target.resolve(location.get()));
    }
  }

  /** Fetches `remote` and records it when the remote answers with 200. */
  private void recordFetched(URI remote) throws IOException {
    try {
      HttpResponse<InputStream> response = get(remote, java.util.Map.of());
      MessageDigest sha256 = sha256();
      ByteArrayOutputStream contents = isMutable(remote) ? new ByteArrayOutputStream() : null;
      try (InputStream body = response.body()) {
        drain(body, sha256, contents);
      }
      if (response.statusCode() == 200) {
        record(remote, sha256, contents);
      }
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    } catch (IOException e) {
      System.err.println("gradle-purifier recorder: " + remote + ": " + e);
    }
  }

  /** Reads `body` to its end into `sha256`, and into `out` when it is not null. */
  private static long drain(InputStream body, MessageDigest sha256, OutputStream out)
      throws IOException {
    byte[] buffer = new byte[64 * 1024];
    long total = 0;
    int n;
    while ((n = body.read(buffer)) != -1) {
      sha256.update(buffer, 0, n);
      if (out != null) {
        out.write(buffer, 0, n);
      }
      total += n;
    }
    return total;
  }

  /**
   * Whether the remote file changes when a new version is published: Maven's version listings
   * (maven-metadata.xml and its checksum files), which Gradle reads for dynamic versions and
   * snapshots. The lock holds such a file's contents rather than a hash of its URL.
   */
  static boolean isMutable(URI remote) {
    String path = remote.getPath();
    return path.substring(path.lastIndexOf('/') + 1).startsWith("maven-metadata");
  }

  /** Only the native Javadoc preparation request can opt these metadata files into inline pins. */
  static boolean isJavadocMetadata(URI remote, String resourceKind) {
    String path = remote.getPath();
    if (!"javadoc-link-metadata".equals(resourceKind) || path == null) return false;
    String filename = path.substring(path.lastIndexOf('/') + 1);
    return filename.equals("element-list") || filename.equals("package-list");
  }

  static boolean isUtf8(byte[] bytes) {
    try {
      StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
          .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes));
      return true;
    } catch (CharacterCodingException e) {
      return false;
    }
  }

  /**
   * Records a fetched file: by its hash, or by its contents when `contents` holds them. A
   * contents record has "inline" in place of the URL and the contents in Base64 in place of the
   * hash.
   */
  private void record(URI remote, MessageDigest sha256, ByteArrayOutputStream contents)
      throws IOException {
    String path = treePathOf(remote);
    if (path.endsWith("/")) {
      return;
    }
    // Generic downloads keep URL/hash representation. Marked Javadoc metadata
    // may be inline only when JSON's UTF-8 text representation preserves every byte.
    if (contents != null && !isMutable(remote) && !isUtf8(contents.toByteArray())) contents = null;
    if (contents != null) {
      record(path, "inline", Base64.getEncoder().encodeToString(contents.toByteArray()), "");
    } else {
      URI mirror = mirrorOf(remote);
      record(
          path,
          remote.toString(),
          "sha256-" + Base64.getEncoder().encodeToString(sha256.digest()),
          mirror == null ? "" : mirror.toString());
    }
  }

  /** Writes to two streams; closing it closes the first only. */
  private static final class TeeOutputStream extends OutputStream {
    private final OutputStream first;
    private final OutputStream second;

    TeeOutputStream(OutputStream first, OutputStream second) {
      this.first = first;
      this.second = second;
    }

    @Override
    public void write(int b) throws IOException {
      first.write(b);
      second.write(b);
    }

    @Override
    public void write(byte[] b, int off, int len) throws IOException {
      first.write(b, off, len);
      second.write(b, off, len);
    }

    @Override
    public void flush() throws IOException {
      first.flush();
    }

    @Override
    public void close() throws IOException {
      first.close();
    }
  }

  private synchronized void record(String path, String url, String hash, String mirror)
      throws IOException {
    records.write(path + "\t" + url + "\t" + hash + "\t" + mirror + "\n");
    records.flush();
  }

  private static MessageDigest sha256() {
    try {
      return MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException(e);
    }
  }
}
