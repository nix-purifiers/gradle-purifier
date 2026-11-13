// SPDX-License-Identifier: MIT
// Read property keys with Java's native Properties grammar. Never change them.
import java.io.File;
import java.io.FileInputStream;
import java.util.Properties;
public final class SdkDiscoveryProperties {
    public static void main(String[] args) throws Exception {
        boolean arguments = false;
        boolean configured = false;
        for (String item : args) {
            if (item.equals("--")) { arguments = true; continue; }
            if (arguments) {
                configured |= item.contains("org.gradle.java.installations.");
            } else {
                File file = new File(item);
                if (!file.exists()) continue;
                Properties properties = new Properties();
                FileInputStream input = new FileInputStream(file);
                try { properties.load(input); } finally { input.close(); }
                for (Object key : properties.keySet()) {
                    configured |= key.toString().contains("org.gradle.java.installations.");
                }
            }
        }
        for (String name : new String[] { "JAVA_OPTS", "GRADLE_OPTS" }) {
            String value = System.getenv(name);
            configured |= value != null && value.contains("org.gradle.java.installations.");
        }
        for (String name : System.getenv().keySet()) {
            configured |= name.startsWith("ORG_GRADLE_PROJECT_") && name.contains("org.gradle.java.installations.");
        }
        System.out.println(configured ? "configured" : "absent");
    }
}
