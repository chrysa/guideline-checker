// Sample TypeScript file with intentional violations
// @ts-ignore next-line
import { Component } from "react";

const fetchUser = async (id: any): Promise<any> => {  // no any
  console.log("fetching", id);  // no console.log
  // @ts-nocheck
  return fetch(`/api/users/${id}`).then((r) => r.json());
};

export const UserCard = ({ user }: { user: any }) => {  // no any
  return (
    <div style={{ color: "red", fontSize: 14 }}>  {/* inline style */}
      {user.name}
    </div>
  );
};
