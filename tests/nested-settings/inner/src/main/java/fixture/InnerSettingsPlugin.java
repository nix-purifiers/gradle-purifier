package fixture;

import org.gradle.api.Plugin;
import org.gradle.api.initialization.Settings;

public final class InnerSettingsPlugin implements Plugin<Settings> {
    public void apply(Settings target) {
        target.getExtensions().getExtraProperties().set("innerApplied", true);
    }
}
