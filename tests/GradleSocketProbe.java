// SPDX-License-Identifier: MIT
import java.io.IOException;
import java.net.*;
import java.nio.channels.*;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Failure-only diagnostics: no JVM properties or sandbox rules are changed. */
public final class GradleSocketProbe {
    private static SocketChannel client(String family) throws IOException {
        return family.equals("default") ? SocketChannel.open()
                : SocketChannel.open(StandardProtocolFamily.valueOf(family));
    }

    private static void child(String[] args) {
        String name = args[1], family = args[2];
        String phase = "client-open";
        try (SocketChannel channel = client(family)) {
            phase = "client-connect";
            channel.socket().connect(new InetSocketAddress(args[3], Integer.parseInt(args[4])), 2000);
            channel.socket().setSoTimeout(2000);
            phase = "bidirectional-echo";
            channel.socket().getOutputStream().write(42);
            if (channel.socket().getInputStream().read() != 43) throw new IOException("client echo mismatch");
            System.out.println(name + " client PASS requestedFamily=" + family
                    + " local=" + channel.getLocalAddress() + " remote=" + channel.getRemoteAddress());
        } catch (Exception error) {
            System.out.println(name + " client FAIL requestedFamily=" + family + " phase=" + phase);
            error.printStackTrace(System.out);
            System.exit(3);
        }
    }

    private static void pair(String source, String name, String clientFamily, String serverFamily, String host) {
        Process child = null;
        String phase = "server-open";
        try (ServerSocketChannel listener = serverFamily.equals("default") ? ServerSocketChannel.open()
                : ServerSocketChannel.open(StandardProtocolFamily.valueOf(serverFamily))) {
            phase = "server-bind";
            listener.bind(new InetSocketAddress(host, 0));
            listener.socket().setSoTimeout(4000);
            System.out.println(name + " server requestedFamily=" + serverFamily + " bound=" + listener.getLocalAddress());
            AtomicReference<Exception> failure = new AtomicReference<>();
            Thread worker = new Thread(() -> {
                try (Socket peer = listener.socket().accept()) {
                    peer.setSoTimeout(2000);
                    if (peer.getInputStream().read() != 42) throw new IOException("server echo mismatch");
                    peer.getOutputStream().write(43);
                } catch (Exception error) {
                    failure.set(error);
                }
            });
            worker.setDaemon(true);
            worker.start();
            phase = "child-process";
            String java = Path.of(System.getProperty("java.home"), "bin", "java").toString();
            child = new ProcessBuilder(java, source, "client", name, clientFamily, host,
                    Integer.toString(((InetSocketAddress) listener.getLocalAddress()).getPort()))
                    .inheritIO().start();
            boolean finished = child.waitFor(6, TimeUnit.SECONDS);
            worker.join(1000);
            if (finished && child.exitValue() == 0 && failure.get() == null) {
                System.out.println(name + " pair PASS crossProcess=true");
            } else {
                System.out.println(name + " pair FAIL child=" + (finished ? child.exitValue() : "timeout")
                        + " server=" + failure.get());
            }
        } catch (Exception error) {
            System.out.println(name + " server FAIL phase=" + phase);
            error.printStackTrace(System.out);
        } finally {
            if (child != null && child.isAlive()) child.destroyForcibly();
        }
    }

    private static boolean external(String family) {
        try (SocketChannel channel = client(family)) {
            // Documentation-only TEST-NET-2: never contact a real service.
            channel.socket().connect(new InetSocketAddress("198.51.100.1", 443), 1500);
            System.out.println("external " + family + " UNEXPECTED_SUCCESS");
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage()).toLowerCase();
            boolean permission = message.contains("operation not permitted") || message.contains("permission denied");
            boolean namespace = System.getProperty("os.name").equals("Linux") && message.contains("network is unreachable");
            if (permission || namespace) {
                System.out.println("external " + family + " DENIED kind=" + (permission ? "permission" : "namespace")
                        + " class=" + error.getClass().getName() + " message=" + error.getMessage());
                return true;
            }
            System.out.println("external " + family + " UNPROVEN class=" + error.getClass().getName()
                    + " message=" + error.getMessage());
        }
        return false;
    }

    public static void main(String[] args) {
        if (args.length > 0 && args[0].equals("client")) {
            child(args);
            return;
        }
        if (args.length != 1) throw new IllegalArgumentException("source file path required");
        for (String property : new String[] {"java.home", "java.version", "java.vendor", "os.arch",
                "java.net.preferIPv4Stack", "java.net.preferIPv6Addresses"}) {
            System.out.println(property + ": " + System.getProperty(property));
        }
        pair(args[0], "default-to-default-v4", "default", "default", "127.0.0.1");
        pair(args[0], "default-to-INET-v4", "default", "INET", "127.0.0.1");
        pair(args[0], "INET-to-INET-v4", "INET", "INET", "127.0.0.1");
        pair(args[0], "INET6-to-INET-v4", "INET6", "INET", "127.0.0.1");
        pair(args[0], "INET6-to-INET6-v6", "INET6", "INET6", "::1");
        boolean denied = true;
        for (String family : new String[] {"default", "INET", "INET6"}) denied &= external(family);
        if (!denied) System.exit(2);
    }
}
