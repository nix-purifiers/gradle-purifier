// SPDX-License-Identifier: MIT

import java.io.File;
import org.gradle.wrapper.PathAssembler;
import org.gradle.wrapper.WrapperConfiguration;
import org.gradle.wrapper.WrapperExecutor;

/**
 * Where the Gradle wrapper of a build looks for its distribution: `gradle-purifier prefetch` puts
 * the locked distribution there, so ./gradlew downloads nothing. It asks the wrapper's own code
 * (PathAssembler, from the gradle-wrapper-shared jar of the distribution), so the paths are the
 * ones the wrapper uses.
 *
 * <p>Usage: {@code java -cp <gradle-wrapper-shared jar> WrapperDistribution.java <Gradle user
 * home> <build root>}. It prints two lines: the directory of the unpacked distribution, and the
 * zip file whose name with ".ok" appended marks it as installed.
 */
public final class WrapperDistribution {

  public static void main(String[] args) {
    File gradleUserHome = new File(args[0]);
    File root = new File(args[1]);
    WrapperConfiguration configuration =
        WrapperExecutor.forWrapperPropertiesFile(
                new File(root, "gradle/wrapper/gradle-wrapper.properties"))
            .getConfiguration();
    PathAssembler.LocalDistribution distribution =
        new PathAssembler(gradleUserHome, root).getDistribution(configuration);
    System.out.println(distribution.getDistributionDir().getAbsolutePath());
    System.out.println(distribution.getZipFile().getAbsolutePath());
  }
}
