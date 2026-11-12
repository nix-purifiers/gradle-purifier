package fixture;

import org.gradle.api.Plugin;
import org.gradle.api.initialization.Settings;

public final class OuterSettingsPlugin implements Plugin<Settings> {
    public void apply(Settings target) {
        target.getExtensions().getExtraProperties().set("outerApplied", true);
    }
}
