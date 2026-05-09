package saturn.foo

import saturn.framework.{Node, Reference, NodeArg, GlobalArg}
import org.apache.spark.sql.DataFrame
import org.apache.spark.sql.Dataset

class FooNode(
    @Reference("saturn", "cdl_d_party") partyDf: DataFrame,
    @NodeArg("reportCode") reportCode: String,
    @NodeArg("reportDescription") reportDescription: String,
    @GlobalArg("lrdCode") lrdCode: String
) extends Node[Common] with ImpalaUtils {
  def run(): Dataset[Common] = ???
}

class FooPersistNode(
    upstream: Dataset[Common],
    @GlobalArg("lrdCode") lrdCode: String
) extends Node[Common] {
  def run(): Dataset[Common] = ???
}
