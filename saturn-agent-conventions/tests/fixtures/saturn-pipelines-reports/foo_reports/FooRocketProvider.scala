package saturn.foo

import saturn.framework.RocketBuilder

class FooRocketProvider extends RocketBuilder("foo_reports") {
  globalArg("lrdCode")
    .globalArg("reportingViewCode")
    .add(new FooNode(_, _, _, _), "foo_workflow", List("foo_persist"))
    .nodeArg("reportCode", "FOO_BO")
    .nodeArg("reportDescription", "Foo Reports")
    .add(new FooPersistNode(_, _), "foo_persist", List())
    .finish()
}
