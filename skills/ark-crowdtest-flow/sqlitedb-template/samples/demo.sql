-- A runnable demo for sqlitedb. Paste line-by-line into the REPL
-- (`python -m sqlitedb`) or run the whole file via the connection API.

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    budget REAL
);

CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    dept_id INTEGER,
    salary REAL,
    hired_year INTEGER
);

CREATE INDEX idx_emp_dept ON employees(dept_id);
CREATE INDEX idx_emp_salary ON employees(salary);

INSERT INTO departments (id, name, budget) VALUES
    (1, 'Engineering', 1000000),
    (2, 'Sales',       500000),
    (3, 'HR',          200000);

INSERT INTO employees (name, dept_id, salary, hired_year) VALUES
    ('Ada Lovelace',   1, 120000, 2019),
    ('Linus Torvalds', 1, 140000, 2017),
    ('Grace Hopper',   1, 130000, 2018),
    ('Madison Li',     2,  90000, 2021),
    ('Nathan Drake',   2,  85000, 2022),
    ('Olive Green',    3,  70000, 2020);

-- who earns the most per department?
SELECT d.name, e.name, e.salary
FROM employees e
JOIN departments d ON e.dept_id = d.id
ORDER BY e.salary DESC
LIMIT 3;

-- headcount and average salary per department
SELECT d.name, COUNT(*), AVG(e.salary)
FROM employees e
JOIN departments d ON e.dept_id = d.id
GROUP BY d.name
ORDER BY d.name;

-- range scan via the salary index
SELECT name, salary FROM employees WHERE salary >= 120000 ORDER BY salary DESC;
