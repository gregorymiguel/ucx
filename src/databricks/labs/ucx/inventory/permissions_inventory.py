import logging

import pandas as pd
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.iam import ObjectPermissions
from pyspark.sql import DataFrame
from pyspark.sql.types import StringType, StructField, StructType

from databricks.labs.ucx.inventory.types import (
    AclItemsContainer,
    LogicalObjectType,
    PermissionsInventoryItem,
    RequestObjectType,
)
from databricks.labs.ucx.providers.spark import SparkMixin

logger = logging.getLogger(__name__)


class PermissionsInventoryTable(SparkMixin):
    def __init__(self, inventory_database: str, ws: WorkspaceClient):
        super().__init__(ws)
        self._table = f"hive_metastore.{inventory_database}.permissions"

    @property
    def _table_schema(self) -> StructType:
        return StructType(
            [
                StructField("object_id", StringType(), True),
                StructField("logical_object_type", StringType(), True),
                StructField("request_object_type", StringType(), True),
                StructField("raw_object_permissions", StringType(), True),
            ]
        )

    @property
    def _df(self) -> DataFrame:
        return self.spark.table(self._table)

    def cleanup(self):
        logger.info(f"Cleaning up inventory table {self._table}")
        self.spark.sql(f"DROP TABLE IF EXISTS {self._table}")
        logger.info("Inventory table cleanup complete")

    def save(self, items: list[PermissionsInventoryItem]):
        # TODO: update instead of append
        logger.info(f"Saving {len(items)} items to inventory table {self._table}")
        serialized_items = pd.DataFrame([item.as_dict() for item in items])
        df = self.spark.createDataFrame(serialized_items, schema=self._table_schema)
        df.write.mode("append").format("delta").saveAsTable(self._table)
        logger.info("Successfully saved the items to inventory table")

    def load_all(self) -> list[PermissionsInventoryItem]:
        logger.info(f"Loading inventory table {self._table}")
        df = self._df.toPandas()

        logger.info("Successfully loaded the inventory table")
        return PermissionsInventoryItem.from_pandas(df)

    @staticmethod
    def _is_item_relevant_to_groups(item: PermissionsInventoryItem, groups: list[str]) -> bool:
        if item.logical_object_type == LogicalObjectType.SECRET_SCOPE:
            _acl_container: AclItemsContainer = item.typed_object_permissions
            return any(acl_item.principal in groups for acl_item in _acl_container.acls)

        elif isinstance(item.request_object_type, RequestObjectType):
            _ops: ObjectPermissions = item.typed_object_permissions
            mentioned_groups = [acl.group_name for acl in _ops.access_control_list]
            return any(g in mentioned_groups for g in groups)

        elif item.logical_object_type in [LogicalObjectType.ENTITLEMENTS, LogicalObjectType.ROLES]:
            return any(g in item.object_id for g in groups)

        else:
            msg = f"Logical object type {item.logical_object_type} is not supported"
            raise NotImplementedError(msg)

    def load_for_groups(self, groups: list[str]) -> list[PermissionsInventoryItem]:
        logger.info(f"Loading inventory table {self._table} and filtering it to relevant groups")
        df = self._df.toPandas()
        all_items = PermissionsInventoryItem.from_pandas(df)
        filtered_items = [item for item in all_items if self._is_item_relevant_to_groups(item, groups)]
        logger.info(f"Found {len(filtered_items)} items relevant to the groups among {len(all_items)} items")
        return filtered_items