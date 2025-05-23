Documentation Source:
airbyte.com/tutorials/incremental-data-synchronization.md

Documentation Title:
Incremental data synchronization between Postgres databases | Airbyte

Documentation Content:
Update: Modify a record in the source

Go back to the Postgres shell in the *airbyte-source*container that you opened earlier in this tutorial, and update a record in *table\_two*and view the table as follows:

`UPDATE table_two SET name='Eg2b DD+Hs' WHERE id=2;
SELECT * FROM table_two;`‍

And the source *table\_two*table should now look as follows:

`id | name | updated_at 
----+------------+----------------------------
 1 | Eg1 DD+Hst | 2022-09-01 16:18:07.569818
 2 | Eg2b DD+Hs | 2022-09-01 17:02:14.841419
(2 rows)`‍

The *name*and the *updated\_at*values have been updated as expected. Execute a new sync by clicking on*Sync Now* in the connections UI and wait for the sync to complete. You should see a response indicating that one record has been emitted, as follows: 

‍

!### Update: View the raw table in the destination

Look at the **incremental dedupe history**raw table called *\_airbyte\_raw\_table\_two*with the following command on the destination Postgres database.



Documentation Source:
airbyte.com/tutorials/incremental-data-synchronization.md

Documentation Title:
Incremental data synchronization between Postgres databases | Airbyte

Documentation Content:
Update: Modify a record in the source

Go back to the Postgres shell in the*airbyte-source* container that you opened earlier in this tutorial, and update a record in *table\_one*and view the table as follows:

`UPDATE table_one SET name='Eg2b IncAp' WHERE id=2;
SELECT * FROM table_one;`‍

And the source *table\_one*table should now look as follows:

`id | name | updated_at 
----+------------+----------------------------
 1 | Eg1 IncApp | 2022-09-01 11:01:41.666004
 2 | Eg2b IncAp | 2022-09-01 11:16:38.093877
(2 rows)`‍

The *name*and the *updated\_at*values have been updated as expected. Execute a new sync by clicking on*Sync Now*in the connections UI and wait for the sync to complete. You should see a response indicating that one record has been emitted, as follows: 

‍

!‍


> ℹ️  Note that opposed to the full sync modes discussed in the previous tutorialwhich would emit all of the records in the source on each sync, in the incremental sync modes only new or modified records are emitted – this is handled in the source connector logic, which only selects new documents in the source for replication to the destination.

A single record has been emitted, which corresponds to the record that was just updated. Additionally, make a note of the*job ID* and the *attempt ID* which in this case are 150 and 0. You will use these values later to view the SQL that has been used for normalization.



Documentation Source:
airbyte.com/tutorials/incremental-data-synchronization.md

Documentation Title:
Incremental data synchronization between Postgres databases | Airbyte

Documentation Content:
Insert: Write a new record in the source

Go back to the Postgres shell in the *airbyte-source*container that you opened earlier in this tutorial, and insert a new record into *table\_two*and view it as follows:

`INSERT INTO table_two(id, name) VALUES(3, 'Eg3 DD+Hst');
SELECT * FROM table_two;`‍

The source *table\_two*table should look as follows:

`id | name | updated_at 
----+------------+----------------------------
 1 | Eg1 DD+Hst | 2022-09-01 16:18:07.569818
 2 | Eg2b DD+Hs | 2022-09-01 17:02:14.841419
 3 | Eg3 DD+Hst | 2022-09-01 17:05:19.570672
(3 rows)`‍

Execute a new sync by clicking on *Sync Now*in the connections UI and wait for the sync to complete. You should see a response indicating that one record has been emitted, as follows: 

!‍



Documentation Source:
airbyte.com/tutorials/incremental-data-synchronization.md

Documentation Title:
Incremental data synchronization between Postgres databases | Airbyte

Documentation Content:
Insert: Overview

In this step you will insert a new record with an *id*of 3 into the source table. This new record will be appended to the raw table in the destination and will be then normalized and appended to the final data table as shown below:

!### Insert: Write a new record in the source

Go back to the Postgres shell in the*airbyte-source*container that you opened earlier in this tutorial, and insert a new record into *table\_one*and view it as follows:

`INSERT INTO table_one(id, name) VALUES(3, 'Eg3 IncApp');
SELECT * FROM table_one;;`‍

The source *table\_one*table should look as follows:

`id | name | updated_at 
----+------------+----------------------------
 1 | Eg1 IncApp | 2022-09-01 11:01:41.666004
 2 | Eg2b IncAp | 2022-09-01 11:16:38.093877
 3 | Eg3 IncApp | 2022-09-01 11:23:30.195854
(3 rows)`‍

Execute a new sync by clicking on*Sync Now*in the connections UI and wait for the sync to complete. 

‍

!‍

You should see that one record has been emitted, which corresponds to the record that you have just inserted into *table\_one*.



