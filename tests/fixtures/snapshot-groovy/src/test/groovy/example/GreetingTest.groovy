// SPDX-License-Identifier: MIT
package example
class GreetingTest extends groovy.util.GroovyTestCase {
    void testGreeting() { assert Greeting.greet('Nix') == 'Hello, Nix!' }
}
