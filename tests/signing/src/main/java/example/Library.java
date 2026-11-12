package example;

/** A library whose release publication requires a signing key. */
public final class Library {
    private Library() {}

    /** Returns the fixture's observable value. */
    public static String value() { return "signed-library"; }
}
